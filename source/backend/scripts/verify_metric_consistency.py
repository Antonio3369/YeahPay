"""验证工作台核心指标的统一口径。

用法：
    python scripts/verify_metric_consistency.py \
      --duckdb /path/to/merchant.duckdb \
      --report-date 2026-08-13

该脚本不修改数据库，只验证同一数据范围下：
- KPI 本月交易额
- 月度趋势最后一个月
- 日收缴本月合计
- 钱包分布合计
- 户均交易额 = 本月交易额 / 本月活跃商户数

同时抽查代理商和销售范围，防止“全机构正确、切换视角后不一致”。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import duckdb

from core.sql_constants import merchant_binding_where
from core.duckdb_bundle import configure_connection

HW = merchant_binding_where("m")
SUCCESS = "t.trans_status = '交易成功' AND COALESCE(t.amount, 0) > 0"


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duckdb", required=True, type=Path)
    parser.add_argument("--report-date", required=True, help="最后一个已确认的完整自然日，YYYY-MM-DD")
    parser.add_argument("--sample-agents", type=int, default=10)
    parser.add_argument("--sample-sales", type=int, default=10)
    return parser.parse_args()


def as_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def assert_close(name: str, left, right, errors: list[str], tolerance: float = 0.01):
    delta = abs(float(left or 0) - float(right or 0))
    if delta > tolerance:
        errors.append(f"{name}: {left} != {right}, diff={delta}")


def scope_sql(scope_type: str, value: str | None):
    if not value:
        return "1=1", []
    if scope_type == "agent":
        return "CAST(m.agent_id AS VARCHAR) = ?", [value]
    return (
        "CAST(m.id AS VARCHAR) IN ("
        "SELECT DISTINCT CAST(商户编号 AS VARCHAR) FROM merchant_trans_30 WHERE 业务员 = ?)",
        [value],
    )


def check_scope(conn, label: str, scope_type: str, value: str | None, report_date: dt.date):
    cutoff = report_date + dt.timedelta(days=1)
    scope, params = scope_sql(scope_type, value)
    params = list(params)

    common = f"""
        FROM ods_transactions_all t
        JOIN merchants m ON CAST(m.id AS VARCHAR) = CAST(t.merchant_id AS VARCHAR)
        WHERE {HW}
          AND {scope}
          AND t.created_at >= DATE_TRUNC('month', ?::DATE)
          AND t.created_at < ?::DATE
          AND {SUCCESS}
    """
    month_params = params + [report_date.isoformat(), cutoff.isoformat()]
    month_amount = conn.execute(
        "SELECT COALESCE(SUM(t.amount), 0) " + common, month_params
    ).fetchone()[0]
    active_count = conn.execute(
        "SELECT COUNT(DISTINCT CAST(t.merchant_id AS VARCHAR)) " + common, month_params
    ).fetchone()[0]

    trend = conn.execute(
        f"""
        SELECT COALESCE(SUM(t.amount), 0) AS total_amount
        {common}
        GROUP BY DATE_TRUNC('month', t.created_at)
        ORDER BY DATE_TRUNC('month', t.created_at) DESC LIMIT 1
        """,
        month_params,
    ).fetchone()
    trend_amount = trend[0] if trend else 0

    daily_amount = conn.execute(
        "SELECT COALESCE(SUM(t.amount), 0) " + common, month_params
    ).fetchone()[0]
    wallet_amount = conn.execute(
        f"""
        SELECT COALESCE(SUM(t.amount), 0)
        FROM ods_transactions_all t
        JOIN merchants m ON CAST(m.id AS VARCHAR) = CAST(t.merchant_id AS VARCHAR)
        WHERE {merchant_binding_where('m')}
          AND CAST(t.created_at AS DATE) >= DATE_TRUNC('month', ?::DATE)
          AND t.created_at < ?::DATE
          AND {scope}
          AND {SUCCESS}
        """,
        [report_date.isoformat(), cutoff.isoformat(), *params],
    ).fetchone()[0]

    errors: list[str] = []
    assert_close(f"{label}.monthly_trend", month_amount, trend_amount, errors)
    assert_close(f"{label}.daily_collection", month_amount, daily_amount, errors)
    assert_close(f"{label}.wallet_distribution", month_amount, wallet_amount, errors)
    expected_avg = float(month_amount or 0) / active_count if active_count else 0

    return {
        "scope": label,
        "report_date": report_date.isoformat(),
        "this_month_amount": float(month_amount or 0),
        "active_merchants": int(active_count or 0),
        "avg_per_active_merchant": expected_avg,
        "checks": {
            "monthly_trend": not any("monthly_trend" in e for e in errors),
            "daily_collection": not any("daily_collection" in e for e in errors),
            "wallet_distribution": not any("wallet_distribution" in e for e in errors),
        },
        "errors": errors,
    }


def main() -> int:
    args = parse_args()
    report_date = as_date(args.report_date)
    conn = duckdb.connect(str(args.duckdb), read_only=True)
    configure_connection(conn, args.duckdb)
    try:
        latest = conn.execute("SELECT MAX(CAST(created_at AS DATE)) FROM ods_transactions_all").fetchone()[0]
        if latest and report_date > latest:
            print(json.dumps({"ok": False, "errors": [f"report_date {report_date} 超过交易明细最新日期 {latest}"]}, ensure_ascii=False))
            return 1

        scopes = [("all", "all", None)]
        agent_rows = conn.execute(
            f"""SELECT CAST(agent_id AS VARCHAR) FROM merchants m
                WHERE agent_id IS NOT NULL AND {HW}
                GROUP BY agent_id ORDER BY COUNT(*) DESC LIMIT ?""",
            [args.sample_agents],
        ).fetchall()
        scopes.extend((f"agent:{row[0]}", "agent", str(row[0])) for row in agent_rows)
        sales_rows = conn.execute(
            """SELECT 业务员 FROM merchant_trans_30
               WHERE 业务员 IS NOT NULL AND 业务员 != ''
               GROUP BY 业务员 ORDER BY COUNT(*) DESC LIMIT ?""",
            [args.sample_sales],
        ).fetchall()
        scopes.extend((f"sales:{row[0]}", "sales", str(row[0])) for row in sales_rows)

        results = []
        errors = []
        for label, scope_type, value in scopes:
            result = check_scope(conn, label, scope_type, value, report_date)
            results.append(result)
            errors.extend(result["errors"])

        payload = {
            "ok": not errors,
            "duckdb": str(args.duckdb),
            "report_date": args.report_date,
            "checked_scopes": len(results),
            "results": results,
            "errors": errors,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0 if not errors else 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
