#!/usr/bin/env python3
"""把候选运行包的一致性结果绑定到 biz.db。"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sqlite3
from pathlib import Path

import duckdb


VIEW_RE = re.compile(r"read_parquet\s*\(\s*'((?:''|[^'])+)'", re.IGNORECASE)
ALLOWED_PARQUET_RE = re.compile(
    r"ods_transactions_all(?:-[0-9a-f]{16})?\.parquet",
    re.IGNORECASE,
)


def bundle_fingerprint(database: Path) -> str:
    database = database.resolve()
    connection = duckdb.connect(str(database), read_only=True)
    search_path = str(database.parent).replace("'", "''")
    connection.execute(f"SET file_search_path='{search_path}'")
    row = connection.execute(
        "SELECT sql FROM duckdb_views() WHERE view_name='ods_transactions_all' "
        "AND internal=false LIMIT 1"
    ).fetchone()
    connection.close()
    files = [database]
    if row and row[0]:
        match = VIEW_RE.search(str(row[0]))
        if match:
            raw = Path(match.group(1).replace("''", "'"))
            external = raw if raw.is_absolute() else database.parent / raw
            external = external.resolve()
            if external.parent != database.parent or not external.is_file():
                raise RuntimeError("候选交易明细 Parquet 缺失或不在数据库同目录")
            if not ALLOWED_PARQUET_RE.fullmatch(external.name):
                raise RuntimeError(f"候选交易明细 Parquet 文件名不合法：{external.name}")
            files.append(external)
    return "|".join(
        f"{path.name}:{path.stat().st_size}:{int(path.stat().st_mtime_ns)}"
        for path in files
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="记录候选数据库同步校验结果")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--sqlite", required=True, type=Path)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--consistent", choices=("true", "false"), required=True)
    args = parser.parse_args()
    fingerprint = bundle_fingerprint(args.database) if args.consistent == "true" else ""
    checked_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    connection = sqlite3.connect(str(args.sqlite))
    try:
        for key, value in (
            ("duckdb_sync_consistent", args.consistent),
            ("duckdb_sync_checked_at", checked_at),
            ("duckdb_sync_batch_id", args.batch_id),
            ("duckdb_sync_candidate_fingerprint", fingerprint),
        ):
            connection.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP",
                (key, value),
            )
        connection.commit()
    finally:
        connection.close()
    print(f"consistent={args.consistent}")
    print(f"batch_id={args.batch_id}")
    print(f"fingerprint={fingerprint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
