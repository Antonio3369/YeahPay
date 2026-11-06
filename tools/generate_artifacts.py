#!/usr/bin/env python3
"""Generate handover artifacts from the frozen source and bundled databases."""

from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "source" / "backend"
MINIPROGRAM = ROOT / "source" / "miniprogram"
DUCKDB_FILE = ROOT / "data" / "merchant.duckdb"
SQLITE_FILE = ROOT / "data" / "biz.db"
MACHINE = ROOT / "machine-readable"
DOCS = ROOT / "docs"


def json_default(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def generate_openapi() -> dict:
    os.environ.update(
        {
            "JWT_SECRET": "handover-artifact-generation-only-32chars",
            "DUCKDB_PATH": str(DUCKDB_FILE),
            "SQLITE_PATH": str(SQLITE_FILE),
            "ENVIRONMENT": "development",
        }
    )
    sys.path.insert(0, str(BACKEND))
    from main import app  # noqa: PLC0415

    schema = app.openapi()
    (MACHINE / "openapi.json").write_text(
        json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return schema


def safe_count_duckdb(conn, name: str) -> int | str:
    try:
        return int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
    except Exception as exc:
        return f"ERROR: {exc}"


def duckdb_dictionary() -> dict:
    import duckdb  # noqa: PLC0415

    conn = duckdb.connect(str(DUCKDB_FILE), read_only=True)
    search_path = str(DUCKDB_FILE.resolve().parent).replace("'", "''")
    conn.execute(f"SET file_search_path='{search_path}'")
    objects = []
    rows = conn.execute(
        """
        SELECT table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = 'main'
        ORDER BY table_name
        """
    ).fetchall()
    date_candidates = (
        "snapshot_date",
        "trans_date",
        "created_at",
        "binding_date",
        "request_time",
        "upload_time",
    )
    for name, object_type in rows:
        columns = []
        for cid, col_name, col_type, notnull, default, pk in conn.execute(
            f'PRAGMA table_info("{name}")'
        ).fetchall():
            columns.append(
                {
                    "position": cid,
                    "name": col_name,
                    "type": col_type,
                    "nullable": not bool(notnull),
                    "default": default,
                    "primary_key": bool(pk),
                }
            )
        col_names = {c["name"] for c in columns}
        coverage = None
        for date_col in date_candidates:
            if date_col in col_names:
                try:
                    minimum, maximum = conn.execute(
                        f'SELECT MIN("{date_col}"), MAX("{date_col}") FROM "{name}"'
                    ).fetchone()
                    coverage = {
                        "column": date_col,
                        "minimum": minimum,
                        "maximum": maximum,
                    }
                except Exception:
                    pass
                break
        objects.append(
            {
                "name": name,
                "object_type": object_type,
                "row_count": safe_count_duckdb(conn, name),
                "date_coverage": coverage,
                "columns": columns,
            }
        )
    conn.close()
    return {
        "engine": "DuckDB + Parquet",
        "file": "data/merchant.duckdb",
        "external_transaction_file": "data/ods_transactions_all-*.parquet",
        "objects": objects,
    }


def sqlite_dictionary() -> dict:
    conn = sqlite3.connect(f"file:{SQLITE_FILE}?mode=ro&immutable=1", uri=True)
    objects = []
    for (name,) in conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ):
        columns = []
        for cid, col_name, col_type, notnull, default, pk in conn.execute(
            f'PRAGMA table_info("{name}")'
        ):
            columns.append(
                {
                    "position": cid,
                    "name": col_name,
                    "type": col_type,
                    "nullable": not bool(notnull),
                    "default": default,
                    "primary_key": bool(pk),
                }
            )
        objects.append(
            {
                "name": name,
                "object_type": "BASE TABLE",
                "row_count": int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]),
                "columns": columns,
            }
        )
    settings = {}
    try:
        settings = dict(conn.execute("SELECT key, value FROM settings ORDER BY key"))
    except sqlite3.Error:
        pass
    conn.close()
    return {
        "engine": "SQLite",
        "file": "data/biz.db",
        "objects": objects,
        "settings_snapshot": settings,
    }


def generate_database_dictionary() -> dict:
    result = {"duckdb": duckdb_dictionary(), "sqlite": sqlite_dictionary()}
    (MACHINE / "database_dictionary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8"
    )
    lines = [
        "# 数据库表与字段字典",
        "",
        "> 本文由随包脚本根据交接数据库自动生成。机器可读完整版见 `machine-readable/database_dictionary.json`。",
        "",
        "## 数据库分工",
        "",
        "- `merchant.duckdb`：只读分析入口，保存商户主数据、汇总结果和交易明细视图。",
        "- `ods_transactions_all-*.parquet`：视图实际读取的全时间范围交易明细；必须与 DuckDB 同目录。",
        "- `biz.db`：可写业务库，保存账号、权限范围、预警、阈值、推送、跟进、复盘及发布日期。",
        "- 分析双文件与 `biz.db` 缺一不可；更换分析运行包时不能覆盖或丢弃 `biz.db`。",
        "",
    ]
    for db_key, title in (("duckdb", "DuckDB"), ("sqlite", "SQLite")):
        lines.extend([f"## {title}", ""])
        for obj in result[db_key]["objects"]:
            coverage = obj.get("date_coverage")
            extra = ""
            if coverage:
                extra = f"；{coverage['column']}：{coverage['minimum']} 至 {coverage['maximum']}"
            lines.extend(
                [
                    f"### `{obj['name']}`",
                    "",
                    f"类型：{obj['object_type']}；行数：{obj['row_count']}{extra}",
                    "",
                    "| 字段 | 类型 | 可空 | 主键 | 默认值 |",
                    "|---|---|---:|---:|---|",
                ]
            )
            for col in obj["columns"]:
                default = "" if col["default"] is None else str(col["default"]).replace("|", "\\|")
                lines.append(
                    f"| `{col['name']}` | `{col['type']}` | {'是' if col['nullable'] else '否'} | "
                    f"{'是' if col['primary_key'] else '否'} | {default} |"
                )
            lines.append("")
    (DOCS / "04_数据库表与字段字典.md").write_text("\n".join(lines), encoding="utf-8")
    return result


def schema_ref(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    if "$ref" in value:
        return value["$ref"].rsplit("/", 1)[-1]
    if "type" in value:
        return str(value["type"])
    for key in ("anyOf", "oneOf", "allOf"):
        if key in value:
            return " / ".join(filter(None, (schema_ref(item) for item in value[key])))
    return ""


def generate_api_catalog(openapi: dict) -> None:
    rows = []
    for path, methods in sorted(openapi.get("paths", {}).items()):
        for method, operation in methods.items():
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            parameters = []
            for param in operation.get("parameters", []):
                parameters.append(
                    f"{param.get('name')}({param.get('in')}{',必填' if param.get('required') else ''})"
                )
            body = operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
            response = operation.get("responses", {}).get("200", {}).get("content", {}).get("application/json", {}).get("schema", {})
            rows.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "summary": operation.get("summary", ""),
                    "auth": "JWT" if operation.get("security") else "无/未声明",
                    "parameters": "；".join(parameters),
                    "request_model": schema_ref(body),
                    "response_model": schema_ref(response),
                }
            )
    with (MACHINE / "api_catalog.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# API 接口与字段说明",
        "",
        "> 精确请求/响应 Schema 以 `machine-readable/openapi.json` 为准；可导入 Swagger、Postman 或其他 OpenAPI 工具。",
        "",
        f"接口总数：{len(rows)}。统一前缀：`/api/v1`；健康检查：`GET /health`。",
        "",
        "| 方法 | 路径 | 用途 | 鉴权 | 参数 | 请求模型 | 响应模型 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        clean = {k: str(v).replace("|", "\\|").replace("\n", " ") for k, v in row.items()}
        lines.append(
            f"| {clean['method']} | `{clean['path']}` | {clean['summary']} | {clean['auth']} | "
            f"{clean['parameters']} | {clean['request_model']} | {clean['response_model']} |"
        )
    lines.extend(
        [
            "",
            "## 通用约定",
            "",
            "- 登录成功后，除登录与健康检查外，前端请求通常携带 `Authorization: Bearer <JWT>`。",
            "- 分页接口以返回体中的 `total` 作为全量统计，不能用当前页 `items.length` 代替。",
            "- 金额存储和接口计算使用 HKD 原值；`万/亿`只属于前端展示格式。",
            "- 报表日期以 `biz.db.settings.data_refresh_date` 为基准，不直接使用服务器自然日。",
            "- 角色数据范围由 JWT 用户和 `user_scope_bindings` 决定，不能接受前端传入任意范围绕过权限。",
        ]
    )
    (DOCS / "03_API接口与字段说明.md").write_text("\n".join(lines), encoding="utf-8")


def generate_page_api_matrix() -> None:
    call_pattern = re.compile(
        r"(?:^|[^A-Za-z0-9_])(get|post|put|delete|patch)\s*\(\s*([`'\"])(.*?)\2",
        re.DOTALL,
    )
    rows = []
    for path in sorted(MINIPROGRAM.rglob("*.js")):
        if "ec-canvas" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "utils/api" not in text:
            continue
        for match in call_pattern.finditer(text):
            endpoint = re.sub(r"\s+", " ", match.group(3)).strip()
            rows.append(
                {
                    "page_or_module": str(path.relative_to(MINIPROGRAM)),
                    "method": match.group(1).upper(),
                    "endpoint_expression": endpoint,
                }
            )
    with (MACHINE / "page_api_field_matrix.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["page_or_module", "method", "endpoint_expression"])
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    MACHINE.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    openapi = generate_openapi()
    generate_api_catalog(openapi)
    generate_database_dictionary()
    generate_page_api_matrix()
    print("Artifacts generated successfully")


if __name__ == "__main__":
    main()
