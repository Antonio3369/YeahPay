"""DuckDB 运行包的外部 Parquet 定位与一致性指纹。"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb


_PARQUET_VIEW_RE = re.compile(
    r"read_parquet\s*\(\s*'((?:''|[^'])+)'",
    re.IGNORECASE,
)
_ALLOWED_PARQUET_RE = re.compile(
    r"ods_transactions_all(?:-[0-9a-f]{16})?\.parquet",
    re.IGNORECASE,
)


def configure_connection(
    connection: duckdb.DuckDBPyConnection,
    database_path: str | Path,
) -> None:
    """让数据库视图从 DuckDB 文件所在目录解析外部文件。"""
    parent = str(Path(database_path).expanduser().resolve().parent)
    connection.execute("SET file_search_path='" + parent.replace("'", "''") + "'")


def external_transaction_file(
    connection: duckdb.DuckDBPyConnection,
    database_path: str | Path,
) -> Path | None:
    """返回 ``ods_transactions_all`` 视图使用的同目录 Parquet。"""
    database = Path(database_path).expanduser().resolve()
    configure_connection(connection, database)
    row = connection.execute(
        "SELECT sql FROM duckdb_views() "
        "WHERE view_name='ods_transactions_all' AND internal=false LIMIT 1"
    ).fetchone()
    if not row or not row[0]:
        return None
    match = _PARQUET_VIEW_RE.search(str(row[0]))
    if not match:
        return None
    raw = match.group(1).replace("''", "'")
    external = Path(raw)
    if external.is_absolute():
        resolved = external.resolve()
    else:
        resolved = (database.parent / external).resolve()
    if resolved.parent != database.parent:
        raise RuntimeError("交易明细 Parquet 必须与 merchant.duckdb 位于同一目录")
    if not _ALLOWED_PARQUET_RE.fullmatch(resolved.name):
        raise RuntimeError(f"交易明细 Parquet 文件名不合法：{resolved.name}")
    return resolved


def bundle_files(database_path: str | Path) -> list[Path]:
    """返回数据库运行所需的全部文件，并确认外部文件存在。"""
    database = Path(database_path).expanduser().resolve()
    if not database.is_file():
        raise FileNotFoundError(f"数据库不存在：{database}")
    connection = duckdb.connect(str(database), read_only=True)
    try:
        external = external_transaction_file(connection, database)
    finally:
        connection.close()
    files = [database]
    if external is not None:
        if not external.is_file():
            raise FileNotFoundError(f"交易明细 Parquet 不存在：{external}")
        files.append(external)
    return files


def bundle_fingerprint(database_path: str | Path) -> str:
    """生成绑定 DuckDB 与外部 Parquet 的候选版本身份。"""
    parts = []
    for path in bundle_files(database_path):
        stat = path.stat()
        parts.append(f"{path.name}:{stat.st_size}:{int(stat.st_mtime_ns)}")
    return "|".join(parts)


def bundle_size(database_path: str | Path) -> int:
    return sum(path.stat().st_size for path in bundle_files(database_path))
