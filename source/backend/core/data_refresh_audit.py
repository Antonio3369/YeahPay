"""DuckDB 候选版本的实体完整性审计与发布后业务数据协调。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from core import sqlite_client
from core.duckdb_bundle import configure_connection
from core.sql_constants import merchant_not_test_sql, test_marker_sql


REQUIRED_COLUMNS = {
    "merchants": {"id", "name", "agent_id", "agent_name", "status", "bound_sn", "bound_qr_code"},
    "merchant_trans_30": {"商户编号", "业务员"},
    "transaction_amounts": {"merchant_id", "trans_date", "amount"},
    "ods_transactions_all": {"merchant_id", "created_at", "trans_status", "amount"},
}


def _columns(conn: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}


def _valid_merchant_sql(alias: str = "m") -> str:
    return (
        f"COALESCE(TRIM(CAST({alias}.status AS VARCHAR)), '') != '注销' AND "
        f"((COALESCE(TRIM(CAST({alias}.bound_sn AS VARCHAR)), '') != '') OR "
        f"(COALESCE(TRIM(CAST({alias}.bound_qr_code AS VARCHAR)), '') != '')) AND "
        f"{merchant_not_test_sql(alias)}"
    )


def inspect_entity_snapshot(path: Path) -> dict[str, Any]:
    """读取候选库中的商户、代理商和销售主数据，不扫描大体量交易明细。"""
    result: dict[str, Any] = {
        "ok": False,
        "issues": [],
        "warnings": [],
        "counts": {},
        "_merchant_ids": set(),
        "_merchant_names": {},
        "_agent_ids": set(),
        "_agent_names": {},
        "_sales_names": set(),
    }
    if not path.is_file():
        result["issues"].append("数据库文件不存在")
        return result

    conn = duckdb.connect(str(path), read_only=True)
    try:
        configure_connection(conn, path)
        table_names = {str(row[0]) for row in conn.execute("SHOW TABLES").fetchall()}
        for table, required in REQUIRED_COLUMNS.items():
            if table not in table_names:
                result["issues"].append(f"缺少必要数据表：{table}")
                continue
            missing = required - _columns(conn, table)
            if missing:
                result["issues"].append(f"{table} 缺少字段：{', '.join(sorted(missing))}")
        if result["issues"]:
            return result

        duplicate_count = int(conn.execute(
            """SELECT COUNT(*) FROM (
                   SELECT CAST(id AS VARCHAR) mid
                   FROM merchants
                   GROUP BY CAST(id AS VARCHAR)
                   HAVING COUNT(*) > 1
               )"""
        ).fetchone()[0])
        if duplicate_count:
            result["issues"].append(f"merchants 存在 {duplicate_count} 个重复商户ID")

        rows = conn.execute(
            f"""SELECT CAST(id AS VARCHAR), COALESCE(CAST(name AS VARCHAR), ''),
                       COALESCE(CAST(agent_id AS VARCHAR), ''),
                       COALESCE(CAST(agent_name AS VARCHAR), '')
                FROM merchants m
                WHERE {_valid_merchant_sql('m')}"""
        ).fetchall()
        merchant_ids = {str(row[0]).strip() for row in rows if str(row[0]).strip()}
        merchant_names = {str(row[0]).strip(): str(row[1]).strip() for row in rows if str(row[0]).strip()}
        agent_ids = {str(row[2]).strip() for row in rows if str(row[2]).strip()}
        agent_names = {
            str(row[2]).strip(): str(row[3]).strip()
            for row in rows if str(row[2]).strip()
        }
        sales_names = {
            str(row[0]).strip()
            for row in conn.execute(
                f"""SELECT DISTINCT CAST(mt.业务员 AS VARCHAR)
                    FROM merchant_trans_30 mt
                    JOIN merchants m ON CAST(m.id AS VARCHAR)=CAST(mt.商户编号 AS VARCHAR)
                    WHERE mt.业务员 IS NOT NULL
                      AND TRIM(CAST(mt.业务员 AS VARCHAR)) != ''
                      AND {_valid_merchant_sql('m')}"""
            ).fetchall()
            if str(row[0]).strip()
        }
        orphan_summary_total = int(conn.execute(
            """SELECT COUNT(*) FROM (
                   SELECT DISTINCT CAST(mt.商户编号 AS VARCHAR) mid
                   FROM merchant_trans_30 mt
                   LEFT JOIN merchants m ON CAST(m.id AS VARCHAR)=CAST(mt.商户编号 AS VARCHAR)
                   WHERE m.id IS NULL
               )"""
        ).fetchone()[0])
        mt_columns = _columns(conn, "merchant_trans_30")
        status_expr = (
            "COALESCE(CAST(mt.商户状态 AS VARCHAR), '')"
            if "商户状态" in mt_columns else "''"
        )
        test_name_exprs = [
            f"mt.{column}" for column in ("商户名称", "一代名称") if column in mt_columns
        ]
        test_expr = test_marker_sql(test_name_exprs) if test_name_exprs else "FALSE"
        orphan_summary_cancelled = int(conn.execute(
            f"""SELECT COUNT(*) FROM (
                   SELECT DISTINCT CAST(mt.商户编号 AS VARCHAR) mid
                   FROM merchant_trans_30 mt
                   LEFT JOIN merchants m ON CAST(m.id AS VARCHAR)=CAST(mt.商户编号 AS VARCHAR)
                   WHERE m.id IS NULL AND {status_expr}='注销'
               )"""
        ).fetchone()[0])
        orphan_summary_test = int(conn.execute(
            f"""SELECT COUNT(*) FROM (
                   SELECT DISTINCT CAST(mt.商户编号 AS VARCHAR) mid
                   FROM merchant_trans_30 mt
                   LEFT JOIN merchants m ON CAST(m.id AS VARCHAR)=CAST(mt.商户编号 AS VARCHAR)
                   WHERE m.id IS NULL
                     AND {status_expr} != '注销'
                     AND {test_expr}
               )"""
        ).fetchone()[0])
        # 这里的 unresolved 才是真正需要同步脚本处理的主表断链；注销和测试
        # 商户属于已识别的业务排除项，不应继续制造“随机孤儿”告警。
        orphan_summary = max(
            0, orphan_summary_total - orphan_summary_cancelled - orphan_summary_test
        )
        all_merchants = int(conn.execute("SELECT COUNT(*) FROM merchants").fetchone()[0])
        cancelled = int(conn.execute(
            "SELECT COUNT(*) FROM merchants WHERE COALESCE(TRIM(CAST(status AS VARCHAR)), '')='注销'"
        ).fetchone()[0])

        result["counts"] = {
            "all_merchants": all_merchants,
            "visible_merchants": len(merchant_ids),
            "cancelled_merchants": cancelled,
            "agents": len(agent_ids),
            "sales": len(sales_names),
            "orphan_summary_total": orphan_summary_total,
            "orphan_summary_cancelled": orphan_summary_cancelled,
            "orphan_summary_test": orphan_summary_test,
            "orphan_summary_merchants": orphan_summary,
        }
        if not merchant_ids:
            result["issues"].append("候选库没有任何有效商户")
        if orphan_summary:
            result["warnings"].append(f"近30天汇总中有 {orphan_summary} 个非测试、非注销商户ID不存在于商户主表")
        result.update({
            "_merchant_ids": merchant_ids,
            "_merchant_names": merchant_names,
            "_agent_ids": agent_ids,
            "_agent_names": agent_names,
            "_sales_names": sales_names,
        })
        result["ok"] = not result["issues"]
        return result
    except Exception as exc:
        result["issues"].append(f"实体审计失败：{exc}")
        return result
    finally:
        conn.close()


def public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in snapshot.items() if not key.startswith("_")}


def _sample(ids: set[str], names: dict[str, str], limit: int = 20) -> list[dict[str, str]]:
    return [{"id": value, "name": names.get(value, "")} for value in sorted(ids)[:limit]]


def compare_entity_snapshots(
    active: dict[str, Any],
    candidate: dict[str, Any],
    max_removal_ratio: float,
) -> dict[str, Any]:
    active_merchants = active.get("_merchant_ids", set())
    candidate_merchants = candidate.get("_merchant_ids", set())
    added_merchants = candidate_merchants - active_merchants
    removed_merchants = active_merchants - candidate_merchants
    active_agents = active.get("_agent_ids", set())
    candidate_agents = candidate.get("_agent_ids", set())
    active_sales = active.get("_sales_names", set())
    candidate_sales = candidate.get("_sales_names", set())
    removal_ratio = len(removed_merchants) / len(active_merchants) if active_merchants else 0.0
    blocked = bool(active_merchants and removal_ratio > max_removal_ratio)

    stale_bindings = []
    for row in sqlite_client.query_all(
        """SELECT b.user_id, u.username, b.scope_type, b.scope_value
           FROM user_scope_bindings b
           JOIN users u ON u.id=b.user_id
           WHERE u.is_active=1"""
    ):
        valid = candidate_agents if row["scope_type"] == "agent" else candidate_sales
        if str(row["scope_value"]) not in valid:
            stale_bindings.append(row)

    return {
        "blocked": blocked,
        "block_reason": (
            f"有效商户一次减少 {len(removed_merchants)} 家（{removal_ratio:.1%}），超过安全阈值 {max_removal_ratio:.0%}"
            if blocked else ""
        ),
        "merchant_added": len(added_merchants),
        "merchant_removed": len(removed_merchants),
        "merchant_removal_ratio": round(removal_ratio * 100, 2),
        "merchant_added_sample": _sample(added_merchants, candidate.get("_merchant_names", {})),
        "merchant_removed_sample": _sample(removed_merchants, active.get("_merchant_names", {})),
        "agent_added": len(candidate_agents - active_agents),
        "agent_removed": len(active_agents - candidate_agents),
        "sales_added": len(candidate_sales - active_sales),
        "sales_removed": len(active_sales - candidate_sales),
        "stale_binding_count": len(stale_bindings),
        "stale_binding_sample": stale_bindings[:20],
    }


def reconcile_operational_entities(valid_merchant_ids: set[str]) -> dict[str, int]:
    """关闭已从有效主数据移除的当前工作流，历史已解决记录保留用于审计。"""
    with sqlite_client._write_lock:
        with sqlite_client.sqlite_session() as db:
            db.execute("CREATE TEMP TABLE valid_refresh_merchants (merchant_id TEXT PRIMARY KEY)")
            db.executemany(
                "INSERT INTO valid_refresh_merchants(merchant_id) VALUES (?)",
                [(merchant_id,) for merchant_id in valid_merchant_ids],
            )
            alerts_closed = db.execute(
                """UPDATE alerts SET status='ignored', updated_at=CURRENT_TIMESTAMP
                   WHERE status IN ('unfollowed','following')
                     AND NOT EXISTS (
                         SELECT 1 FROM valid_refresh_merchants v
                         WHERE v.merchant_id=CAST(alerts.merchant_id AS TEXT)
                     )"""
            ).rowcount
            pushes_closed = db.execute(
                """UPDATE merchant_pushes SET status='ignored', updated_at=CURRENT_TIMESTAMP
                   WHERE status='following'
                     AND NOT EXISTS (
                         SELECT 1 FROM valid_refresh_merchants v
                         WHERE v.merchant_id=CAST(merchant_pushes.merchant_id AS TEXT)
                     )"""
            ).rowcount
            return {"alerts_closed": alerts_closed, "pushes_closed": pushes_closed}
