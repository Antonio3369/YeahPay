#!/usr/bin/env python3
"""把完整 DuckDB 转换为小程序只读运行包（DuckDB + Parquet）。"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import uuid
from pathlib import Path

import duckdb


TRANSACTION_COLUMNS = (
    "created_at",
    "order_no",
    "merchant_id",
    "merchant_name",
    "merchant_short_name",
    "agent_id",
    "trans_channel",
    "pay_wallet",
    "amount",
    "trans_type",
    "trans_status",
    "error_code",
    "error_msg",
    "masked_card_no",
    "card_type",
    "card_location",
)

RUNTIME_TABLES = (
    "ads_merchant_status_snapshots",
    "dim_merchant_list",
    "dim_sales_person",
    "dws_daily_active_from_excel",
    "dws_merchant_stats",
    "merchant_trans_30",
    "merchants",
    "transaction_amounts",
    "transaction_counts",
)

PARQUET_NAME_RE = re.compile(r"ods_transactions_all-[0-9a-f]{16}\.parquet")


def quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def set_search_path(connection: duckdb.DuckDBPyConnection, directory: Path) -> None:
    value = str(directory.resolve()).replace("'", "''")
    connection.execute(f"SET file_search_path='{value}'")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def data_signature(connection: duckdb.DuckDBPyConnection, relation: str) -> tuple:
    columns = ", ".join(quote(column) for column in TRANSACTION_COLUMNS)
    row_hash = f"hash({columns})"
    return connection.execute(
        f"SELECT COUNT(*), COALESCE(bit_xor({row_hash}), 0), "
        f"COALESCE(sum({row_hash}), 0) FROM {relation}"
    ).fetchone()


def build(source: Path, output_dir: Path, replace: bool, max_total_mb: int) -> tuple[Path, Path]:
    source = source.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_database = output_dir / "merchant.duckdb"
    if output_database.exists() and not replace:
        raise RuntimeError(f"输出已存在，请使用 --replace：{output_database}")

    token = uuid.uuid4().hex
    temporary_parquet = output_dir / f".ods_transactions_all.{token}.parquet.tmp"
    temporary_database = output_dir / f".merchant.{token}.duckdb.tmp"
    old_parquets = set(output_dir.glob("ods_transactions_all-*.parquet"))
    columns = ", ".join(quote(column) for column in TRANSACTION_COLUMNS)

    try:
        source_connection = duckdb.connect(str(source), read_only=True)
        set_search_path(source_connection, source.parent)
        available = {row[0] for row in source_connection.execute("SHOW TABLES").fetchall()}
        required = set(RUNTIME_TABLES) | {"ods_transactions_all"}
        missing = sorted(required - available)
        if missing:
            raise RuntimeError("源数据库缺少运行表：" + ", ".join(missing))
        source_signature = data_signature(source_connection, "ods_transactions_all")
        source_counts = {
            table: source_connection.execute(f"SELECT COUNT(*) FROM {quote(table)}").fetchone()[0]
            for table in RUNTIME_TABLES
        }
        source_connection.execute(
            f"COPY (SELECT {columns} FROM ods_transactions_all ORDER BY merchant_id, created_at) "
            f"TO '{str(temporary_parquet).replace(chr(39), chr(39) * 2)}' "
            "(FORMAT PARQUET, COMPRESSION ZSTD, COMPRESSION_LEVEL 22, ROW_GROUP_SIZE 122880)"
        )
        source_connection.close()

        parquet_digest = sha256(temporary_parquet)
        parquet = output_dir / f"ods_transactions_all-{parquet_digest[:16]}.parquet"
        if parquet.exists():
            if sha256(parquet) != parquet_digest:
                raise RuntimeError(f"同名 Parquet 内容不一致：{parquet}")
            temporary_parquet.unlink()
        else:
            os.replace(temporary_parquet, parquet)

        destination = duckdb.connect(str(temporary_database))
        set_search_path(destination, output_dir)
        destination.execute(f"ATTACH '{str(source).replace(chr(39), chr(39) * 2)}' AS source (READ_ONLY)")
        for table in RUNTIME_TABLES:
            destination.execute(
                f"CREATE TABLE {quote(table)} AS SELECT * FROM source.main.{quote(table)}"
            )
        destination.execute("DETACH source")
        destination.execute(
            f"CREATE VIEW ods_transactions_all AS SELECT * FROM read_parquet('{parquet.name}')"
        )
        destination.execute("CHECKPOINT")
        destination.close()

        validation = duckdb.connect(str(temporary_database), read_only=True)
        set_search_path(validation, output_dir)
        runtime_signature = data_signature(validation, "ods_transactions_all")
        if runtime_signature != source_signature:
            raise RuntimeError(
                f"交易明细校验失败：source={source_signature}, runtime={runtime_signature}"
            )
        for table, expected in source_counts.items():
            actual = validation.execute(f"SELECT COUNT(*) FROM {quote(table)}").fetchone()[0]
            if actual != expected:
                raise RuntimeError(f"{table} 行数不一致：{expected} != {actual}")
        validation.close()

        total_size = temporary_database.stat().st_size + parquet.stat().st_size
        if total_size > max_total_mb * 1024 * 1024:
            raise RuntimeError(
                f"运行包 {total_size / 1024 / 1024:.1f}MiB，超过限制 {max_total_mb}MiB"
            )
        os.replace(temporary_database, output_database)
        for old in old_parquets - {parquet}:
            old.unlink(missing_ok=True)
        return output_database, parquet
    except Exception:
        temporary_parquet.unlink(missing_ok=True)
        temporary_database.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 YeahPay 小程序只读运行数据库包")
    parser.add_argument("--source", required=True, type=Path, help="完整 DuckDB 路径")
    parser.add_argument("--output-dir", required=True, type=Path, help="运行包输出目录")
    parser.add_argument("--replace", action="store_true", help="替换输出目录中的旧运行包")
    parser.add_argument("--max-total-mb", type=int, default=120)
    args = parser.parse_args()
    database, parquet = build(args.source, args.output_dir, args.replace, args.max_total_mb)
    total = database.stat().st_size + parquet.stat().st_size
    print(f"DuckDB: {database} ({database.stat().st_size} bytes)")
    print(f"Parquet: {parquet} ({parquet.stat().st_size} bytes)")
    print(f"Total: {total} bytes ({total / 1024 / 1024:.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
