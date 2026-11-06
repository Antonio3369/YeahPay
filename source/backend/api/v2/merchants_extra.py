"""
v2 商户接口 - 完整版
1. list  商户列表（含6月交易额/7月累计/日均环比）
2. kpi   商户详情日/周/月环比
"""
from fastapi import APIRouter, Depends, Query, HTTPException
from core import duckdb_client
from core.auth import get_current_user, resolve_scope_values
from api.scope import ensure_merchant_access
from core.sql_constants import MERCHANT_BINDING_WHERE_M, successful_transaction_where
from api.v2.scope_helper import build_scope_filter

router = APIRouter(prefix="/v2/merchants", tags=["v2-商户"])


@router.get("/list")
def merchant_list(
    keyword: str = Query(None),
    agent_id: str = Query(None),
    sales_person: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user=Depends(get_current_user),
):
    """商户列表 - 完整版（含6月/7月交易额+日均环比）"""
    # 构建销售过滤
    conditions = ["1=1"]
    params = []
    agent_ids, sales_names = resolve_scope_values(current_user, agent_id, sales_person)
    if agent_ids or sales_names:
        parts = []
        if agent_ids:
            placeholders = ",".join("?" for _ in agent_ids)
            parts.append(f"CAST(m.agent_id AS VARCHAR) IN ({placeholders})")
            params.extend(agent_ids)
        if sales_names:
            placeholders = ",".join("?" for _ in sales_names)
            parts.append(
                "CAST(m.id AS VARCHAR) IN (SELECT DISTINCT CAST(商户编号 AS VARCHAR) "
                f"FROM merchant_trans_30 WHERE 业务员 IN ({placeholders}))"
            )
            params.extend(sales_names)
        conditions.append("(" + " OR ".join(parts) + ")")
    elif current_user.get("role") != "admin":
        conditions.append("1=0")
    if keyword:
        conditions.append("(CAST(m.id AS VARCHAR) LIKE ? OR m.name LIKE ? OR m.short_name LIKE ?)")
        params.extend([f"%{keyword}%"] * 3)

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    total = duckdb_client.query_one(
        f"SELECT COUNT(*) as cnt FROM merchants m WHERE {where} AND {MERCHANT_BINDING_WHERE_M}", params
    )["cnt"]

    rows = duckdb_client.query_all(
        f"""
        SELECT
            m.id, m.name, m.short_name, m.agent_name, m.status,
            COALESCE(SUM(CASE WHEN t.created_at >= DATE_TRUNC('month', report_date()) - INTERVAL 1 MONTH
                AND t.created_at < DATE_TRUNC('month', report_date()) THEN t.amount ELSE 0 END), 0) AS last_month_amount,
            COALESCE(SUM(CASE WHEN t.created_at >= DATE_TRUNC('month', report_date()) THEN t.amount ELSE 0 END), 0) AS this_month_amount
        FROM merchants m
        LEFT JOIN ods_transactions_all t ON CAST(t.merchant_id AS VARCHAR) = CAST(m.id AS VARCHAR)
            AND t.created_at >= DATE_TRUNC('month', report_date()) - INTERVAL 1 MONTH
            AND t.created_at < report_cutoff()
            AND {successful_transaction_where('t')}
        WHERE {where} AND {MERCHANT_BINDING_WHERE_M}
        GROUP BY m.id, m.name, m.short_name, m.agent_name, m.status
        ORDER BY last_month_amount DESC, m.id DESC
        LIMIT ? OFFSET ?
        """,
        params + [page_size, offset],
    )

    # 计算日均环比
    import datetime
    now = duckdb_client.get_reporting_date()
    import calendar
    # report_date() 是已确认的完整数据日，当前月应包含该日。
    days_this = now.day
    previous_month = 12 if now.month == 1 else now.month - 1
    previous_year = now.year - 1 if now.month == 1 else now.year
    days_last = calendar.monthrange(previous_year, previous_month)[1]
    for r in rows:
        last = r["last_month_amount"] or 0
        this = r["this_month_amount"] or 0
        avg_last = last / days_last if days_last > 0 else 0
        avg_this = this / days_this if days_this > 0 else 0
        if avg_last > 0:
            r["daily_mom_pct"] = round((avg_this - avg_last) / avg_last * 100, 1)
            r["trend"] = "up" if r["daily_mom_pct"] >= 0 else "down"
        else:
            r["daily_mom_pct"] = None
            r["trend"] = "neutral"

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < total,
        "items": rows,
    }


@router.get("/{merchant_id}/kpi")
def merchant_kpi(merchant_id: str, current_user=Depends(get_current_user)):
    """商户详情 - 日/周/月环比"""
    ensure_merchant_access(merchant_id, current_user)
    # 日环比：前30日均值 vs 昨日
    daily = duckdb_client.query_one(
        f"""
        WITH stats AS (
            SELECT
                COALESCE(SUM(CASE WHEN created_at >= report_cutoff() - INTERVAL 31 DAY
                    AND created_at < report_date() THEN amount ELSE 0 END) / 30, 0) AS avg_30d,
                COALESCE(SUM(CASE WHEN created_at >= report_date()
                    AND created_at < report_cutoff() THEN amount ELSE 0 END), 0) AS yesterday
            FROM ods_transactions_all
            WHERE CAST(merchant_id AS VARCHAR) = ?
              AND {successful_transaction_where('ods_transactions_all')}
        )
        SELECT avg_30d, yesterday,
            CASE WHEN avg_30d > 0 THEN ROUND((yesterday - avg_30d) / avg_30d * 100, 1) ELSE NULL END AS daily_pct
        FROM stats
        """,
        [merchant_id],
    )

    # 周环比：上周 vs 上上周
    weekly = duckdb_client.query_one(
        f"""
        WITH stats AS (
            SELECT
                COALESCE(SUM(CASE WHEN created_at >= report_cutoff() - INTERVAL 7 DAY AND created_at < report_cutoff() THEN amount ELSE 0 END), 0) AS this_week,
                COALESCE(SUM(CASE WHEN created_at >= report_cutoff() - INTERVAL 14 DAY AND created_at < report_cutoff() - INTERVAL 7 DAY THEN amount ELSE 0 END), 0) AS last_week
            FROM ods_transactions_all
            WHERE CAST(merchant_id AS VARCHAR) = ?
              AND {successful_transaction_where('ods_transactions_all')}
        )
        SELECT this_week, last_week,
            CASE WHEN last_week > 0 THEN ROUND((this_week - last_week) / last_week * 100, 1) ELSE NULL END AS weekly_pct
        FROM stats
        """,
        [merchant_id],
    )

    # 月环比：本月完整日 vs 上月同期
    import datetime
    import calendar
    today = duckdb_client.get_reporting_date()
    # 发布日期当天已经完整入库，月度同期比较也必须包含这一天。
    completed_days = today.day
    previous_month = 12 if today.month == 1 else today.month - 1
    previous_year = today.year - 1 if today.month == 1 else today.year
    compare_days = min(completed_days, calendar.monthrange(previous_year, previous_month)[1])
    monthly = duckdb_client.query_one(
        f"""
        WITH stats AS (
            SELECT
                COALESCE(SUM(CASE WHEN created_at >= DATE_TRUNC('month', report_date())
                    AND created_at < report_cutoff() THEN amount ELSE 0 END), 0) AS this_month,
                COALESCE(SUM(CASE WHEN created_at >= DATE_TRUNC('month', report_date()) - INTERVAL 1 MONTH
                    AND created_at < DATE_TRUNC('month', report_date()) - INTERVAL 1 MONTH
                        + INTERVAL '{compare_days}' DAY THEN amount ELSE 0 END), 0) AS last_month
            FROM ods_transactions_all
            WHERE CAST(merchant_id AS VARCHAR) = ?
              AND {successful_transaction_where('ods_transactions_all')}
        )
        SELECT this_month, last_month,
            CASE WHEN last_month > 0 THEN ROUND((this_month - last_month) / last_month * 100, 1) ELSE NULL END AS monthly_pct
        FROM stats
        """,
        [merchant_id],
    )

    # 月度趋势（近3个月）
    trend = duckdb_client.query_all(
        f"""
        SELECT
            DATE_TRUNC('month', created_at) AS month,
            COALESCE(SUM(amount), 0) AS amount
        FROM ods_transactions_all
        WHERE CAST(merchant_id AS VARCHAR) = ?
          AND created_at >= DATE_TRUNC('month', report_date()) - INTERVAL 2 MONTH
          AND created_at < report_cutoff()
          AND {successful_transaction_where('ods_transactions_all')}
        GROUP BY 1 ORDER BY 1
        """,
        [merchant_id],
    )
    for t in trend:
        t["month"] = str(t["month"])

    return {
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
        "monthly_trend": trend,
    }
