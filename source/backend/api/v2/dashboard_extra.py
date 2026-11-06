"""
v2 工作台接口 - 6个新接口
1. alert-summary  预警横幅数字
2. kpi            KPI卡片+问候卡
3. monthly-trend  月度趋势折线图
4. mom-compare    环比双柱图
5. merchant-distribution  商户动态4环形图
6. daily-collection       日收缴双线图
"""
from fastapi import APIRouter, Depends, Query
from core import duckdb_client, sqlite_client
from core.auth import get_current_user, resolve_scope_values
from api.v2.scope_helper import build_scope_filter, resolve_eligible_merchant_filter
from core.sql_constants import merchant_binding_where, successful_transaction_where
from api.scope import scope_merchant_ids as get_scope_merchant_ids

router = APIRouter(prefix="/v2/dashboard", tags=["v2-工作台"])


@router.get("/alert-summary")
def alert_summary(
    agent_id: str = Query(None, description="代理商ID"),
    sales_person: str = Query(None, description="销售名"),
    current_user=Depends(get_current_user),
):
    """1. 预警横幅数字（从 SQLite alerts 表聚合，支持视角过滤）"""
    scope_merchant_ids = get_scope_merchant_ids(current_user, agent_id, sales_person)
    # 视角过滤
    if scope_merchant_ids is not None:
        if len(scope_merchant_ids) == 0:
            return {"total": 0, "following": 0, "unfollowed": 0, "pending_reply": 0}
        placeholders = ",".join(["?"] * len(scope_merchant_ids))
        where = f"WHERE merchant_id IN ({placeholders})"
        params = scope_merchant_ids
    else:
        where = ""
        params = []

    rows = sqlite_client.query_all(
        f"SELECT status, COUNT(*) as cnt FROM alerts {where} GROUP BY status",
        params,
    )
    stats = {"unfollowed": 0, "following": 0, "resolved": 0, "ignored": 0}
    for r in rows:
        stats[r["status"]] = r["cnt"]

    total = sum(stats.values())
    return {
        "total": total,
        "following": stats["following"],
        "unfollowed": stats["unfollowed"],
        "pending_reply": 0,
    }


@router.get("/kpi")
def kpi(
    agent_id: str = Query(None, description="代理商ID"),
    sales_person: str = Query(None, description="销售名"),
    current_user=Depends(get_current_user),
):
    """2. KPI卡片 + 问候卡数据"""
    # 统一有效商户范围：未注销、已绑定硬件、属于当前账号/临时视角。
    eligible_where, eligible_params = resolve_eligible_merchant_filter(
        current_user, agent_id, sales_person, "m"
    )
    merchant_count = duckdb_client.query_one(
        f"SELECT COUNT(*) AS cnt FROM merchants m WHERE {eligible_where}",
        eligible_params,
    ).get("cnt", 0)

    # 活跃商户与金额完全同口径：有效商户 + 交易成功 + 正金额 + 完整日期。
    active_merchant_count = duckdb_client.query_one(
        f"""
        SELECT COUNT(DISTINCT CAST(t.merchant_id AS VARCHAR)) AS cnt
        FROM ods_transactions_all t
        JOIN merchants m ON CAST(m.id AS VARCHAR) = CAST(t.merchant_id AS VARCHAR)
        WHERE {eligible_where}
          AND t.created_at >= DATE_TRUNC('month', report_date())
          AND t.created_at < report_cutoff()
          AND {successful_transaction_where('t')}
        """,
        eligible_params,
    ).get("cnt", 0)
    active_rate = round(active_merchant_count / merchant_count * 100, 1) if merchant_count else 0

    # 截至发布日期（含当天）的近3个完整自然日失败记录。
    fail_stats = duckdb_client.query_one(
        f"""
        SELECT COUNT(*) as fail_count,
               COUNT(DISTINCT t.merchant_id) as fail_merchants
        FROM ods_transactions_all t
        JOIN merchants m ON CAST(m.id AS VARCHAR) = CAST(t.merchant_id AS VARCHAR)
        WHERE t.trans_status = '交易失败'
          AND t.created_at >= report_cutoff() - INTERVAL 3 DAY
          AND t.created_at < report_cutoff()
          AND {eligible_where}
        """,
        eligible_params,
    )

    # 本月 MTD vs 上月同期（同口径对比）
    # 本月：本月1日至已确认发布日期（含当天）
    # 上月：上月1日至上月同日（天数相同，公平对比）
    import datetime
    import calendar
    today = duckdb_client.get_reporting_date()
    # 发布日期来自完整候选库，因此当天是完整数据，可以纳入计算。
    days_this_month = today.day
    # 上月天数（用于计算上月同期截止日）
    if today.month == 1:
        days_prev_month = calendar.monthrange(today.year - 1, 12)[1]
    else:
        days_prev_month = calendar.monthrange(today.year, today.month - 1)[1]
    # 上月同期天数 = min(本月已过天数, 上月总天数)
    days_compare = min(days_this_month, days_prev_month)

    # 上月同期活跃商户数：用于计算户均交易额，时间口径与本月/上月金额一致。
    prev_active_merchant_count = duckdb_client.query_one(
        f"""
        SELECT COUNT(DISTINCT CAST(t.merchant_id AS VARCHAR)) AS cnt
        FROM ods_transactions_all t
        JOIN merchants m ON CAST(m.id AS VARCHAR) = CAST(t.merchant_id AS VARCHAR)
        WHERE {eligible_where}
          AND t.created_at >= DATE_TRUNC('month', report_date()) - INTERVAL 1 MONTH
          AND t.created_at < DATE_TRUNC('month', report_date()) - INTERVAL 1 MONTH
              + INTERVAL '{days_compare}' DAY
          AND {successful_transaction_where('t')}
        """,
        eligible_params,
    ).get("cnt", 0)

    month_stats = duckdb_client.query_one(
        f"""
        SELECT
          COALESCE(SUM(CASE WHEN t.created_at >= DATE_TRUNC('month', report_date())
            AND t.created_at < report_cutoff() AND {successful_transaction_where('t')}
            THEN t.amount ELSE 0 END), 0) as this_month,
          COALESCE(SUM(CASE WHEN t.created_at >= DATE_TRUNC('month', report_date()) - INTERVAL 1 MONTH
            AND t.created_at < DATE_TRUNC('month', report_date()) - INTERVAL 1 MONTH + INTERVAL '{days_compare}' DAY
            AND {successful_transaction_where('t')}
            THEN t.amount ELSE 0 END), 0) as last_month,
          COUNT(CASE WHEN t.created_at >= DATE_TRUNC('month', report_date())
            AND t.created_at < report_cutoff() THEN 1 END) AS this_txn_count,
          COUNT(CASE WHEN t.created_at >= DATE_TRUNC('month', report_date())
            AND t.created_at < report_cutoff() AND t.trans_status = '交易失败' THEN 1 END) AS this_failed_count
        FROM ods_transactions_all t
        JOIN merchants m ON CAST(m.id AS VARCHAR) = CAST(t.merchant_id AS VARCHAR)
        WHERE t.created_at >= DATE_TRUNC('month', report_date()) - INTERVAL 1 MONTH
          AND {eligible_where}
        """,
        eligible_params,
    )

    this_month = month_stats.get("this_month", 0) or 0
    last_month = month_stats.get("last_month", 0) or 0
    mom_pct = round((this_month - last_month) / last_month * 100, 1) if last_month > 0 else 0
    this_month_avg = this_month / active_merchant_count if active_merchant_count > 0 else 0
    last_month_avg = last_month / prev_active_merchant_count if prev_active_merchant_count > 0 else 0
    failed_count = month_stats.get("this_failed_count", 0) or 0
    txn_count = month_stats.get("this_txn_count", 0) or 0
    fail_rate = round(failed_count / txn_count * 100, 2) if txn_count > 0 else 0

    # 日均环比：本月日均 vs 上月同期日均
    avg_this = this_month / days_this_month if days_this_month > 0 else 0
    avg_last = last_month / days_compare if days_compare > 0 else 0
    daily_mom_pct = round((avg_this - avg_last) / avg_last * 100, 1) if avg_last > 0 else 0

    # 预警未跟进数：SQLite 与 DuckDB 分离，先按当前视角取商户 ID 再过滤
    alert_scope_ids = None
    alert_scope_ids = get_scope_merchant_ids(current_user, agent_id, sales_person)

    if alert_scope_ids is None:
        alert_unfollowed = sqlite_client.query_one(
            "SELECT COUNT(*) as cnt FROM alerts WHERE status = 'unfollowed'"
        ).get("cnt", 0)
    elif not alert_scope_ids:
        alert_unfollowed = 0
    else:
        placeholders = ",".join(["?"] * len(alert_scope_ids))
        alert_unfollowed = sqlite_client.query_one(
            f"SELECT COUNT(*) as cnt FROM alerts WHERE status = 'unfollowed' AND CAST(merchant_id AS VARCHAR) IN ({placeholders})",
            alert_scope_ids,
        ).get("cnt", 0)

    return {
        "alert_count": alert_unfollowed,
        "merchant_count": merchant_count,
        "active_merchant_count": active_merchant_count,
        "active_rate": active_rate,
        "this_month_avg": this_month_avg,
        "last_month_avg": last_month_avg,
        "fail_rate": fail_rate,
        "fail_count": fail_stats.get("fail_count", 0),
        "fail_merchants": fail_stats.get("fail_merchants", 0),
        "this_month_amount": this_month,
        "last_month_amount": last_month,
        "mom_pct": mom_pct,
        "daily_mom_pct": daily_mom_pct,
    }


@router.get("/monthly-trend")
def monthly_trend(
    months: int = Query(6, ge=1, le=24, description="最近N个月"),
    agent_id: str = Query(None),
    sales_person: str = Query(None),
    current_user=Depends(get_current_user),
):
    """3. 月度交易趋势折线图"""
    # “最近 N 个月”包含发布日期所在月，因此回溯 N-1 个完整月。
    window_months = months - 1
    eligible_where, params = resolve_eligible_merchant_filter(
        current_user, agent_id, sales_person, "m"
    )
    sql = f"""
        SELECT
            DATE_TRUNC('month', t.created_at) AS month,
            COALESCE(SUM(t.amount), 0) AS total_amount,
            COUNT(*) AS txn_count
        FROM ods_transactions_all t
        JOIN merchants m ON CAST(m.id AS VARCHAR) = CAST(t.merchant_id AS VARCHAR)
        WHERE t.created_at >= DATE_TRUNC('month', report_date()) - INTERVAL '{window_months}' MONTH
          AND t.created_at < report_cutoff()
          AND {successful_transaction_where('t')}
          AND {eligible_where}
        GROUP BY 1
        ORDER BY 1
    """
    rows = duckdb_client.query_all(sql, params)
    for r in rows:
        r["month"] = str(r["month"])
        r["total_amount_wan"] = round(r["total_amount"] / 10000, 0)
    return rows


@router.get("/mom-compare")
def mom_compare(
    agent_id: str = Query(None),
    sales_person: str = Query(None),
    current_user=Depends(get_current_user),
):
    """4. 环比双柱图（上月 vs 本月）"""
    eligible_where, params = resolve_eligible_merchant_filter(
        current_user, agent_id, sales_person, "m"
    )
    sql = f"""
        SELECT
            DATE_TRUNC('month', t.created_at) AS month,
            COALESCE(SUM(t.amount), 0) AS total_amount
        FROM ods_transactions_all t
        JOIN merchants m ON CAST(m.id AS VARCHAR) = CAST(t.merchant_id AS VARCHAR)
        WHERE t.created_at >= DATE_TRUNC('month', report_date()) - INTERVAL 1 MONTH
          AND t.created_at < report_cutoff()
          AND {successful_transaction_where('t')}
          AND {eligible_where}
        GROUP BY 1
        ORDER BY 1
    """
    rows = duckdb_client.query_all(sql, params)
    for r in rows:
        r["month"] = str(r["month"])
        r["total_amount_wan"] = round(r["total_amount"] / 10000, 0)
    return rows


@router.get("/merchant-distribution")
def merchant_distribution(
    current_user=Depends(get_current_user),
    agent_id: str = Query(None),
    sales_person: str = Query(None),
    scope: tuple = Depends(build_scope_filter),
):
    """5. 商户动态4环形图（上涨/下跌中/新沉默/下跌）"""
    # 直接复用公共视角依赖的解析结果，避免同名 Query 参数在 FastAPI
    # 的嵌套依赖和路由函数之间出现不一致。dws 快照只有 merchant_id，
    # 因此把 agent 视角先转换成商户 ID 再过滤。
    scoped_ids = get_scope_merchant_ids(current_user, agent_id, sales_person)

    params = []
    scope_sql = "1=1"
    if scoped_ids is not None:
        if not scoped_ids:
            return {"rising": 0, "declining": 0, "new_silent": 0, "falling": 0, "total": 0,
                    "rising_pct": 0, "declining_pct": 0, "new_silent_pct": 0, "falling_pct": 0}
        placeholders = ",".join(["?"] * len(scoped_ids))
        scope_sql = f"CAST(s.merchant_id AS VARCHAR) IN ({placeholders})"
        params = scoped_ids

    # 从 dws_merchant_stats 取最新快照
    sql = f"""
        SELECT
            CASE
                WHEN vol_recent_30d = 0 AND vol_prev_30d > 0 THEN 'new_silent'
                WHEN vol_recent_30d < vol_prev_30d * 0.9 THEN 'declining'
                WHEN vol_recent_30d > vol_prev_30d * 1.1 THEN 'rising'
                ELSE 'falling'
            END AS trend,
            COUNT(*) AS count
        FROM dws_merchant_stats s
        JOIN merchants m ON CAST(m.id AS VARCHAR) = CAST(s.merchant_id AS VARCHAR)
        WHERE s.snapshot_date = (
            SELECT MAX(snapshot_date) FROM dws_merchant_stats
            WHERE CAST(snapshot_date AS DATE) <= report_date()
        )
          AND {merchant_binding_where('m')}
          AND {scope_sql}
        GROUP BY 1
    """
    rows = duckdb_client.query_all(sql, params)
    result = {"rising": 0, "declining": 0, "new_silent": 0, "falling": 0}
    for r in rows:
        result[r["trend"]] = r["count"]

    total = sum(result.values())
    result["total"] = total
    result["rising_pct"] = round(result["rising"] / total * 100, 1) if total > 0 else 0
    result["declining_pct"] = round(result["declining"] / total * 100, 1) if total > 0 else 0
    result["new_silent_pct"] = round(result["new_silent"] / total * 100, 1) if total > 0 else 0
    result["falling_pct"] = round(result["falling"] / total * 100, 1) if total > 0 else 0

    return result


@router.get("/daily-collection")
def daily_collection(
    days: int = Query(31, ge=1, le=31, description="天数"),
    agent_id: str = Query(None),
    sales_person: str = Query(None),
    current_user=Depends(get_current_user),
):
    """6. 日收缴双线图（本月 vs 上月）"""
    import calendar
    report_date = duckdb_client.get_reporting_date()
    previous_month = 12 if report_date.month == 1 else report_date.month - 1
    previous_year = report_date.year - 1 if report_date.month == 1 else report_date.year
    days_compare = min(report_date.day, calendar.monthrange(previous_year, previous_month)[1])
    eligible_where, params = resolve_eligible_merchant_filter(
        current_user, agent_id, sales_person, "m"
    )
    sql = f"""
        SELECT
            EXTRACT(DAY FROM t.created_at) AS day,
            COALESCE(SUM(CASE WHEN t.created_at >= DATE_TRUNC('month', report_date())
                THEN t.amount ELSE 0 END), 0) AS this_month,
            COALESCE(SUM(CASE WHEN t.created_at >= DATE_TRUNC('month', report_date()) - INTERVAL 1 MONTH
                AND t.created_at < DATE_TRUNC('month', report_date()) - INTERVAL 1 MONTH
                    + INTERVAL '{days_compare}' DAY
                THEN t.amount ELSE 0 END), 0) AS last_month
        FROM ods_transactions_all t
        JOIN merchants m ON CAST(m.id AS VARCHAR) = CAST(t.merchant_id AS VARCHAR)
        WHERE (
            (t.created_at >= DATE_TRUNC('month', report_date()) - INTERVAL 1 MONTH
             AND t.created_at < DATE_TRUNC('month', report_date()) - INTERVAL 1 MONTH
                 + INTERVAL '{days_compare}' DAY)
            OR
            (t.created_at >= DATE_TRUNC('month', report_date())
             AND t.created_at < report_cutoff())
        )
          AND {successful_transaction_where('t')}
          AND {eligible_where}
        GROUP BY 1
        ORDER BY 1
    """
    rows = duckdb_client.query_all(sql, params)
    for r in rows:
        r["day"] = int(r["day"])
        r["this_month_wan"] = round(r["this_month"] / 10000, 0)
        r["last_month_wan"] = round(r["last_month"] / 10000, 0)
    return rows

@router.get("/data-date")
def data_date(_=Depends(get_current_user)):
    """返回当前小程序可见的已发布日期，而不是原始库最大日期。"""
    return {"latest_date": duckdb_client.get_reporting_date().isoformat()}



@router.get("/wallet-distribution")
def wallet_distribution(
    agent_id: str = Query(None),
    sales_person: str = Query(None),
    user=Depends(get_current_user),
):
    """全機構 · 按錢包類型（交易額+筆數）"""
    eligible_where, params = resolve_eligible_merchant_filter(
        user, agent_id, sales_person, "m"
    )
    result = duckdb_client.query_all(f"""
        SELECT
            COALESCE(t.pay_wallet, 'UNKNOWN') AS name,
            COALESCE(SUM(t.amount), 0) AS volume,
            COUNT(*) AS cnt
        FROM ods_transactions_all t
        JOIN merchants m ON CAST(m.id AS VARCHAR) = CAST(t.merchant_id AS VARCHAR)
        WHERE t.created_at >= DATE_TRUNC('month', report_date())
          AND t.created_at < report_cutoff()
          AND {successful_transaction_where('t')}
          AND {eligible_where}
        GROUP BY 1
        ORDER BY volume DESC, name ASC
        LIMIT 12
    """, params=params)
    return result


@router.get("/status-distribution")
def status_distribution(
    agent_id: str = Query(None),
    sales_person: str = Query(None),
    user=Depends(get_current_user),
):
    """全機構 · 按交易狀態（绝对金额+笔数，不与成功交易额 KPI 混用）"""
    eligible_where, params = resolve_eligible_merchant_filter(
        user, agent_id, sales_person, "m"
    )
    result = duckdb_client.query_all(f"""
        SELECT
            COALESCE(t.trans_status, 'UNKNOWN') AS name,
            COALESCE(SUM(ABS(t.amount)), 0) AS volume,
            COUNT(*) AS cnt
        FROM ods_transactions_all t
        JOIN merchants m ON CAST(m.id AS VARCHAR) = CAST(t.merchant_id AS VARCHAR)
        WHERE t.created_at >= DATE_TRUNC('month', report_date())
          AND t.created_at < report_cutoff()
          AND {eligible_where}
        GROUP BY 1
        ORDER BY volume DESC, name ASC
        LIMIT 8
    """, params=params)
    return result
