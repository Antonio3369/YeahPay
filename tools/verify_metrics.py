#!/usr/bin/env python3
"""Offline sanity checks for the handover databases."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import duckdb


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duckdb", type=Path, required=True)
    parser.add_argument("--sqlite", type=Path, required=True)
    args = parser.parse_args()
    if not args.duckdb.is_file() or not args.sqlite.is_file():
        raise SystemExit("数据库文件不存在")

    sqlite = sqlite3.connect(f"file:{args.sqlite}?mode=ro&immutable=1", uri=True)
    integrity = sqlite.execute("PRAGMA integrity_check").fetchone()[0]
    report_date = sqlite.execute(
        "SELECT value FROM settings WHERE key = 'data_refresh_date'"
    ).fetchone()
    sqlite.close()
    if integrity != "ok":
        raise SystemExit(f"SQLite integrity_check 失败: {integrity}")

    db = duckdb.connect(str(args.duckdb), read_only=True)
    search_path = str(args.duckdb.resolve().parent).replace("'", "''")
    db.execute(f"SET file_search_path='{search_path}'")
    required = {
        "merchants",
        "ods_transactions_all",
        "transaction_amounts",
        "transaction_counts",
    }
    tables = {row[0] for row in db.execute("SHOW TABLES").fetchall()}
    missing = sorted(required - tables)
    if missing:
        raise SystemExit("DuckDB 缺少核心表: " + ", ".join(missing))
    counts = {
        name: db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        for name in sorted(required)
    }
    latest_detail = db.execute("SELECT MAX(created_at) FROM ods_transactions_all").fetchone()[0]
    latest_summary = db.execute("SELECT MAX(trans_date) FROM transaction_amounts").fetchone()[0]
    db.close()

    print(f"SQLite: ok; data_refresh_date={report_date[0] if report_date else 'MISSING'}")
    print(f"DuckDB core row counts: {counts}")
    print(f"交易明细最新时间: {latest_detail}")
    print(f"近30天汇总最新日期: {latest_summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
