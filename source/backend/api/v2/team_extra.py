"""
v2 飞虎队接口 - 完整版
字段：销售名/商户数/上月交易额/本月MTD/日均环比/新沉默/下跌中/上涨/未跟进预警
支持 sort_by 排序 + scope 视角切换
"""
from fastapi import APIRouter, Depends, Query
from core import duckdb_client, sqlite_client
from core.auth import get_current_user, require_roles, resolve_scope_values
from core.sql_constants import merchant_binding_where, successful_transaction_where

router = APIRouter(prefix="/v2/team", tags=["v2-飞虎队"])


def _build_scoped_merchant_ids(agent_ids=None, sales_names=None):
    """
    根据 scope 参数返回有权限的商户 id 列表
    - sales_person: 从 merchant_trans_30 查
    - agent_id: 从 merchants 表查
    两者都传时取并集（账号可同时绑定代理商与额外销售）
    返回 None 表示无过滤
    """
    sales_ids = None
    if sales_names:
        placeholders = ",".join("?" for _ in sales_names)
        rows = duckdb_client.query_all(
            f"SELECT DISTINCT CAST(商户编号 AS VARCHAR) AS mid FROM merchant_trans_30 WHERE 业务员 IN ({placeholders})",
            sales_names,
        )
        sales_ids = [m["mid"] for m in rows]

    agent_scope_ids = None
    if agent_ids:
        placeholders = ",".join("?" for _ in agent_ids)
        rows = duckdb_client.query_all(
            f"SELECT DISTINCT CAST(id AS VARCHAR) AS mid FROM merchants WHERE CAST(agent_id AS VARCHAR) IN ({placeholders})",
            agent_ids,
        )
        agent_scope_ids = [m["mid"] for m in rows]

    if sales_ids is not None and agent_scope_ids is not None:
        return list(set(sales_ids) | set(agent_scope_ids))
    elif sales_ids is not None:
        return sales_ids
    elif agent_scope_ids is not None:
        return agent_scope_ids
    else:
        return None


@router.get("/rank")
def team_rank(
    sort_by: str = Query("this_month_amount", description="排序字段"),
    sort_order: str = Query("desc", description="asc/desc"),
    agent_id: str = Query(None, description="代理商ID"),
    sales_person: str = Query(None, description="销售名"),
    current_user: dict = Depends(require_roles("admin", "supervisor")),
):
    """飞虎队业绩榜 - 完整版"""
    # 权限隔离：非 admin 强制使用账号绑定的多范围
    agent_ids, sales_names = resolve_scope_values(current_user, agent_id, sales_person)

    scoped_ids = _build_scoped_merchant_ids(agent_ids, sales_names)
    if current_user["role"] != "admin" and not agent_ids and not sales_names:
        scoped_ids = []
    if scoped_ids is not None and len(scoped_ids) == 0:
        return []

    # 构造 IN 子句
    if scoped_ids is not None:
        placeholders = ",".join(["?"] * len(scoped_ids))
        scope_in_clause = f"CAST(merchant_id AS VARCHAR) IN ({placeholders})"
        cte_in_clause = f"CAST(商户编号 AS VARCHAR) IN ({placeholders})"
        cte_id_clause = f"CAST(merchant_id AS VARCHAR) IN ({placeholders})"
        # 每个 IN 子句都需要自己的参数副本
        p1 = list(scoped_ids)  # sales_merchants CTE
        p2 = list(scoped_ids)  # merchant_trends CTE
        p3 = list(scoped_ids)  # t JOIN 过滤
    else:
        scope_in_clause = "1=1"
        cte_in_clause = "1=1"
        cte_id_clause = "1=1"
        p1 = p2 = p3 = []

    sql = f"""
        WITH sales_merchants AS (
            SELECT DISTINCT mt.业务员 AS sales_name, CAST(mt.商户编号 AS VARCHAR) AS mid
            FROM merchant_trans_30 mt
            JOIN merchants m ON CAST(m.id AS VARCHAR)=CAST(mt.商户编号 AS VARCHAR)
            WHERE mt.业务员 IS NOT NULL AND mt.业务员 != ''
              AND {merchant_binding_where('m')}
              AND {cte_in_clause}
        ),
        merchant_trends AS (
            SELECT
                CAST(s.merchant_id AS VARCHAR) AS mid,
                CASE
                    WHEN s.vol_recent_30d = 0 AND s.vol_prev_30d > 0 THEN 'new_silent'
                    WHEN s.vol_recent_30d < s.vol_prev_30d * 0.9 THEN 'declining'
                    WHEN s.vol_recent_30d > s.vol_prev_30d * 1.1 THEN 'rising'
                    ELSE 'falling'
                END AS trend
            FROM dws_merchant_stats s
            WHERE s.snapshot_date = (
                SELECT MAX(snapshot_date) FROM dws_merchant_stats
                WHERE CAST(snapshot_date AS DATE) <= report_date()
            )
              AND {cte_id_clause}
        )
        SELECT
            sm.sales_name,
            COUNT(DISTINCT sm.mid) AS merchant_count,
            COALESCE(SUM(CASE WHEN t.created_at >= DATE_TRUNC('month', report_date()) - INTERVAL 1 MONTH
                AND t.created_at < DATE_TRUNC('month', report_date()) THEN t.amount ELSE 0 END), 0) AS last_month_amount,
            COALESCE(SUM(CASE WHEN t.created_at >= DATE_TRUNC('month', report_date()) THEN t.amount ELSE 0 END), 0) AS this_month_amount,
            COUNT(DISTINCT CASE WHEN t.amount > 0 AND t.created_at >= DATE_TRUNC('month', report_date()) THEN sm.mid END) AS active_merchants,
            COUNT(DISTINCT CASE WHEN mt.trend = 'new_silent' THEN sm.mid END) AS new_silent_count,
            COUNT(DISTINCT CASE WHEN mt.trend = 'declining' THEN sm.mid END) AS declining_count,
            COUNT(DISTINCT CASE WHEN mt.trend = 'rising' THEN sm.mid END) AS rising_count
        FROM sales_merchants sm
        LEFT JOIN ods_transactions_all t
            ON CAST(t.merchant_id AS VARCHAR) = sm.mid
            AND {scope_in_clause}
            AND t.created_at < report_cutoff()
            AND {successful_transaction_where('t')}
        LEFT JOIN merchant_trends mt ON mt.mid = sm.mid
        GROUP BY sm.sales_name
    """
    all_params = p1 + p2 + p3
    rows = duckdb_client.query_all(sql, all_params)

    # 2. 计算日均环比
    import datetime
    now = duckdb_client.get_reporting_date()
    import calendar
    days_this = now.day
    previous_month = 12 if now.month == 1 else now.month - 1
    previous_year = now.year - 1 if now.month == 1 else now.year
    days_last = calendar.monthrange(previous_year, previous_month)[1]
    for r in rows:
        last = r["last_month_amount"] or 0
        this = r["this_month_amount"] or 0
        avg_last = last / days_last if days_last > 0 else 0
        avg_this = this / days_this if days_this > 0 else 0
        r["daily_mom_pct"] = round((avg_this - avg_last) / avg_last * 100, 1) if avg_last > 0 else 0
        r["last_month_amount_wan"] = round(last / 10000, 0)
        r["this_month_amount_wan"] = round(this / 10000, 0)
        active = r.get("active_merchants", 0) or 0
        r["avg_per_active_wan"] = round(this / 10000 / active, 1) if active > 0 else 0

    # 3. 补充未跟进预警数
    alert_counts = sqlite_client.query_all(
        """SELECT u.display_name AS sales_name, COUNT(*) AS cnt
           FROM alerts a JOIN users u ON u.id = a.assigned_to
           WHERE a.status = 'unfollowed' GROUP BY u.display_name"""
    )
    alert_map = {a["sales_name"]: a["cnt"] for a in alert_counts}

    # 4. 补充用户信息
    users = sqlite_client.query_all("SELECT username, display_name, role FROM users WHERE is_active = 1")
    user_map = {u["display_name"]: u for u in users}

    for r in rows:
        r["unfollowed_alerts"] = alert_map.get(r["sales_name"], 0)
        u = user_map.get(r["sales_name"], {})
        r["username"] = u.get("username", "")
        r["role"] = u.get("role", "")

    # 5. 排序
    valid_sorts = ["this_month_amount", "last_month_amount", "daily_mom_pct", "new_silent_count", "declining_count", "rising_count", "unfollowed_alerts", "merchant_count", "active_merchants", "avg_per_active_wan"]
    if sort_by not in valid_sorts:
        sort_by = "this_month_amount"

    def sort_key(r):
        v = r.get(sort_by, 0)
        if v is None:
            return 0
        return v

    # 先固定同值时的销售名次序，再做稳定的主指标排序，避免底层物理
    # 存储顺序改变后列表相同数据却发生随机换位。
    rows.sort(key=lambda row: str(row.get("sales_name", "")))
    rows.sort(key=sort_key, reverse=(sort_order == "desc"))
    return rows
