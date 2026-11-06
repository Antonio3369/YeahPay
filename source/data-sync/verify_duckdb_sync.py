#!/usr/bin/env python3
"""Compare the logical contents of the local and cloud DuckDB data tables.

The DuckDB files themselves are not expected to have the same binary hash.
This script produces stable, order-independent fingerprints for the tables
used by the mini-program, together with row counts and date coverage.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import duckdb


TABLES = (
    "merchants",
    "merchant_trans_30",
    "ods_transactions_all",
    "transaction_amounts",
    "transaction_counts",
    "dws_merchant_stats",
    "ads_merchant_status_snapshots",
    "dws_daily_active_from_excel",
)

TRANSACTION_COLUMNS = (
    "created_at", "order_no", "merchant_id", "merchant_name",
    "merchant_short_name", "agent_id", "trans_channel", "pay_wallet",
    "amount", "trans_type", "trans_status", "error_code", "error_msg",
    "masked_card_no", "card_type", "card_location",
)


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def quote_table(value: str) -> str:
    return quote_identifier(value)


def text_value(value: object) -> str:
    return "" if value is None else str(value)


def table_report(connection: duckdb.DuckDBPyConnection, table: str) -> str:
    table_exists = connection.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = 'main' AND table_name = ?",
        [table],
    ).fetchone()[0]
    if not table_exists:
        return f"{table}|MISSING|MISSING|MISSING|MISSING|MISSING"

    available_columns = [
        row[1]
        for row in connection.execute(
            f"PRAGMA table_info({quote_table(table)})"
        ).fetchall()
    ]
    columns = (
        list(TRANSACTION_COLUMNS)
        if table == "ods_transactions_all"
        else available_columns
    )
    missing = sorted(set(columns) - set(available_columns))
    if missing:
        return f"{table}|MISSING_COLUMNS:{','.join(missing)}|MISSING|MISSING|MISSING|MISSING"
    if not columns:
        return f"{table}|0|0|0||"

    column_sql = ", ".join(quote_identifier(column) for column in columns)
    row_digest = f"md5(to_json(row({column_sql})))"
    digest_hi = f"CAST('0x' || SUBSTR({row_digest}, 1, 16) AS UBIGINT)"
    digest_lo = f"CAST('0x' || SUBSTR({row_digest}, 17, 16) AS UBIGINT)"
    query = (
        f"SELECT COUNT(*), "
        f"COALESCE(SUM({digest_hi}), 0), "
        f"COALESCE(SUM({digest_lo}), 0) "
        f"FROM {quote_table(table)}"
    )
    count, digest_hi_sum, digest_lo_sum = connection.execute(query).fetchone()

    coverage_min = ""
    coverage_max = ""
    if table == "ods_transactions_all":
        coverage_min, coverage_max = connection.execute(
            "SELECT MIN(created_at), MAX(created_at) FROM ods_transactions_all"
        ).fetchone()
    elif table in {"transaction_amounts", "transaction_counts", "dws_merchant_stats", "ads_merchant_status_snapshots"}:
        coverage_min, coverage_max = connection.execute(
            f"SELECT MIN(snapshot_date), MAX(snapshot_date) FROM {quote_table(table)}"
            if table in {"dws_merchant_stats", "ads_merchant_status_snapshots"}
            else f"SELECT MIN(trans_date), MAX(trans_date) FROM {quote_table(table)}"
        ).fetchone()
    elif table == "merchant_trans_30":
        daily_columns = [
            column
            for column in available_columns
            if re.fullmatch(r"\d{4}笔数", column)
        ]
        if daily_columns:
            daily_keys = sorted(column[:4] for column in daily_columns)
            coverage_min, coverage_max = daily_keys[0], daily_keys[-1]
    elif table == "dws_daily_active_from_excel":
        coverage_min, coverage_max = connection.execute(
            "SELECT MIN(snapshot_date), MAX(snapshot_date) "
            "FROM dws_daily_active_from_excel"
        ).fetchone()

    return "|".join(
        (
            table,
            text_value(count),
            text_value(digest_hi_sum),
            text_value(digest_lo_sum),
            text_value(coverage_min),
            text_value(coverage_max),
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="输出 DuckDB 逻辑一致性指纹")
    parser.add_argument("database", type=Path, help="DuckDB 文件路径")
    args = parser.parse_args()

    if not args.database.is_file():
        print(f"数据库不存在: {args.database}", file=sys.stderr)
        return 2

    try:
        connection = duckdb.connect(str(args.database), read_only=True)
        search_path = str(args.database.resolve().parent).replace("'", "''")
        connection.execute(f"SET file_search_path='{search_path}'")
        for table in TABLES:
            print(table_report(connection, table))
        connection.close()
    except Exception as exc:  # pragma: no cover - CLI diagnostic path
        print(f"一致性检查失败: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
