"""
DuckDB 只读客户端
用途: 连接 merchant.duckdb 执行分析查询（只读，不写入）

关键修复: 每个请求创建独立连接，避免并发冲突
"""
import datetime
import duckdb
from core.config import settings
from core.duckdb_bundle import configure_connection


_GATED_TABLES = {
    "ods_transactions_all": "created_at",
    "transaction_amounts": "trans_date",
    "transaction_counts": "trans_date",
    "dws_merchant_stats": "snapshot_date",
    "ads_merchant_status_snapshots": "snapshot_date",
    "dws_daily_active_from_excel": "snapshot_date",
}


def _confirmed_release_date():
    from core import sqlite_client
    row = sqlite_client.query_one("SELECT value FROM settings WHERE key='data_refresh_date'")
    value = str(row.get("value", ""))[:10] if row else ""
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError:
        return ""


def get_reporting_date() -> datetime.date:
    """返回已确认发布日期；缺失时拒绝读取业务数据。

    业务查询不能退回服务器当天，否则数据库尚未发布的新数据会被提前展示，
    也会让“本月/上月同期”随服务器时钟漂移。
    """
    value = _confirmed_release_date()
    if not value:
        raise RuntimeError("缺少有效的 data_refresh_date，已拒绝读取未确认数据")
    return datetime.date.fromisoformat(value)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _apply_release_gate(conn):
    """用临时视图把所有带日期的核心表限制到管理员已确认日期。

    生产环境必须 fail-closed：确认日期缺失或门控建立失败时拒绝查询，避免
    未确认数据被静默展示。开发环境允许没有历史设置的旧数据库直接读取。
    """
    if not settings.DUCKDB_RELEASE_GATE_ENABLED:
        return
    release_date = _confirmed_release_date()
    if not release_date:
        raise RuntimeError("缺少有效的 data_refresh_date，已拒绝读取未确认数据")

    db_rows = conn.execute("PRAGMA database_list").fetchall()
    db_name = next((row[1] for row in db_rows if row[2]), db_rows[0][1])
    catalog = _quote_identifier(db_name)
    for table, date_column in _GATED_TABLES.items():
        exists = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name=?",
            [table],
        ).fetchone()[0]
        if not exists:
            continue
        table_q = _quote_identifier(table)
        column_q = _quote_identifier(date_column)
        conn.execute(
            f"CREATE OR REPLACE TEMP VIEW {table_q} AS "
            f"SELECT * FROM {catalog}.main.{table_q} "
            f"WHERE CAST({column_q} AS DATE) <= DATE '{release_date}'"
        )


def _apply_reporting_context(conn):
    report_date = get_reporting_date().isoformat()
    conn.execute(f"CREATE OR REPLACE TEMP MACRO report_date() AS DATE '{report_date}'")
    # report_date() 是已发布数据的最后一个完整自然日。时间戳查询统一使用
    # 左闭右开区间，并以次日零点作为上界，确保发布日期当天被完整计入。
    conn.execute(
        f"CREATE OR REPLACE TEMP MACRO report_cutoff() AS DATE '{report_date}' + INTERVAL 1 DAY"
    )


def get_duckdb_connection():
    """获取 DuckDB 只读连接（每次新建，避免并发冲突）"""
    conn = duckdb.connect(
        settings.DUCKDB_PATH,
        read_only=True,
    )
    configure_connection(conn, settings.DUCKDB_PATH)
    conn.execute(f"SET memory_limit='{settings.DUCKDB_MEMORY_LIMIT}'")
    conn.execute(f"SET threads={settings.DUCKDB_THREADS}")
    try:
        _apply_reporting_context(conn)
        _apply_release_gate(conn)
    except Exception:
        conn.close()
        raise
    return conn


def query_all(sql: str, params=None):
    """执行查询，返回 list[dict]"""
    conn = get_duckdb_connection()
    try:
        if params:
            result = conn.execute(sql, params).fetchall()
        else:
            result = conn.execute(sql).fetchall()
        if not result:
            return []
        columns = [d[0] for d in conn.description] if conn.description else []
        if not columns:
            return []
        return [dict(zip(columns, row)) for row in result]
    finally:
        conn.close()


def query_one(sql: str, params=None):
    """执行查询，返回单个 dict（第一条记录）"""
    rows = query_all(sql, params)
    return rows[0] if rows else {}
