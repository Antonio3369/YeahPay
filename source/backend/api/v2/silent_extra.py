"""
v2 沉默商户接口 — 按用户 real 口径
数据来源: DuckDB merchants + ads_merchant_status_snapshots + transaction_amounts

口径:
- 沉睡商户: first_bound_time 14-180天 AND total_volume < 300 AND 硬件绑定 AND status != 注销
- 风险商户: first_bound_time >180天 AND total_volume < 100 AND 近90天无正金额交易 AND 硬件绑定
"""
from fastapi import APIRouter, Depends, Query
from core import duckdb_client, sqlite_client
from core.auth import get_current_user, resolve_scope_values
from core.sql_constants import MERCHANT_BINDING_WHERE_M

router = APIRouter(prefix="/v2/silent", tags=["v2-沉默商户"])


def _silent_thresholds():
    defaults = {
        "silent_days": 14,
        "silent_max_days": 180,
        "silent_amount": 300,
        "risky_days": 180,
        "risky_amount": 100,
        "risky_lookback_days": 90,
    }
    rows = sqlite_client.query_all(
        "SELECT threshold_key, value FROM thresholds WHERE is_enabled = 1"
    )
    for row in rows:
        if row["threshold_key"] in defaults and row["value"] is not None:
            defaults[row["threshold_key"]] = row["value"]
    return defaults


def _silent_scope_clause(agent_ids=None, sales_names=None):
    """沉默商户的 scope 过滤（用 merchants 表字段）"""
    conditions = ["1=1"]
    parts = []
    if agent_ids:
        placeholders = ",".join("?" for _ in agent_ids)
        parts.append(f"CAST(m.agent_id AS VARCHAR) IN ({placeholders})")
    if sales_names:
        placeholders = ",".join("?" for _ in sales_names)
        parts.append(
            "CAST(m.id AS VARCHAR) IN (SELECT DISTINCT CAST(商户编号 AS VARCHAR) "
            f"FROM merchant_trans_30 WHERE 业务员 IN ({placeholders}))"
        )
    if parts:
        conditions.append("(" + " OR ".join(parts) + ")")
    elif agent_ids is not None or sales_names is not None:
        conditions.append("1=0")
    return conditions


@router.get("/summary")
def summary(
    current_user=Depends(get_current_user),
    agent_id: str = Query(None),
    sales_person: str = Query(None),
):
    """沉默商户统计"""
    agent_ids, sales_names = resolve_scope_values(current_user, agent_id, sales_person)
    # 管理员未选择具体视角时代表“全部机构”。空列表不能传给
    # _silent_scope_clause，否则会被解释成 1=0，导致页面永远没有数据。
    if current_user.get("role") == "admin" and not agent_ids and not sales_names:
        agent_ids = sales_names = None
    # 非管理员没有绑定范围时必须保留空列表，下游会生成 1=0，禁止退化为全量。
    t = _silent_thresholds()
    silent_days = int(t["silent_days"])
    silent_max_days = int(t["silent_max_days"])
    silent_amount = float(t["silent_amount"])
    risky_days = int(t["risky_days"])
    risky_amount = float(t["risky_amount"])
    risky_lookback_days = int(t["risky_lookback_days"])
    conditions = _silent_scope_clause(agent_ids, sales_names)
    params = []
    params.extend(agent_ids or [])
    params.extend(sales_names or [])

    where = " AND ".join(conditions)
    sql = f"""
        SELECT
            CASE 
                WHEN report_date() - CAST(m.first_bound_time AS DATE) BETWEEN {silent_days} AND {silent_max_days}
                     AND s.total_volume < {silent_amount} THEN 'sleeping'
                WHEN report_date() - CAST(m.first_bound_time AS DATE) > {risky_days}
                     AND s.total_volume < {risky_amount}
                     AND NOT EXISTS (
                         SELECT 1 FROM transaction_amounts ta
                         WHERE ta.merchant_id = m.id
                           AND ta.trans_date >= report_cutoff() - INTERVAL {risky_lookback_days} DAY
                           AND ta.amount > 0
                     ) THEN 'risky'
            END AS merchant_type,
            COUNT(*) AS count,
            COALESCE(SUM(s.total_volume), 0) AS total_history_amount
        FROM merchants m
        JOIN ads_merchant_status_snapshots s
            ON s.merchant_id = m.id
            AND s.snapshot_date = (
                SELECT MAX(snapshot_date) FROM ads_merchant_status_snapshots
                WHERE CAST(snapshot_date AS DATE) <= report_date()
            )
        WHERE {MERCHANT_BINDING_WHERE_M}
          AND m.first_bound_time IS NOT NULL
          AND report_date() - CAST(m.first_bound_time AS DATE) >= {silent_days}
          AND {where}
          AND (
            (report_date() - CAST(m.first_bound_time AS DATE) BETWEEN {silent_days} AND {silent_max_days} AND s.total_volume < {silent_amount})
            OR
            (report_date() - CAST(m.first_bound_time AS DATE) > {risky_days} AND s.total_volume < {risky_amount}
             AND NOT EXISTS (
                 SELECT 1 FROM transaction_amounts ta
                 WHERE ta.merchant_id = m.id
                   AND ta.trans_date >= report_cutoff() - INTERVAL {risky_lookback_days} DAY
                   AND ta.amount > 0
             ))
          )
        GROUP BY 1
    """
    rows = duckdb_client.query_all(sql, params)
    result = {"sleeping": 0, "risky": 0, "sleeping_amount": 0, "risky_amount": 0}
    for r in rows:
        if r["merchant_type"] == "sleeping":
            result["sleeping"] = r["count"]
            result["sleeping_amount"] = r["total_history_amount"]
        elif r["merchant_type"] == "risky":
            result["risky"] = r["count"]
            result["risky_amount"] = r["total_history_amount"]
    result["total"] = result["sleeping"] + result["risky"]
    result["thresholds"] = {
        "silent_days": silent_days,
        "silent_max_days": silent_max_days,
        "silent_amount": silent_amount,
        "risky_days": risky_days,
        "risky_amount": risky_amount,
        "risky_lookback_days": risky_lookback_days,
    }
    return result


@router.get("/list")
def list_silent(
    merchant_type: str = Query("all"),
    keyword: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user=Depends(get_current_user),
    agent_id: str = Query(None),
    sales_person: str = Query(None),
):
    """沉默商户列表"""
    agent_ids, sales_names = resolve_scope_values(current_user, agent_id, sales_person)
    if current_user.get("role") == "admin" and not agent_ids and not sales_names:
        agent_ids = sales_names = None
    # 非管理员没有绑定范围时必须保留空列表，下游会生成 1=0，禁止退化为全量。
    t = _silent_thresholds()
    silent_days = int(t["silent_days"])
    silent_max_days = int(t["silent_max_days"])
    silent_amount = float(t["silent_amount"])
    risky_days = int(t["risky_days"])
    risky_amount = float(t["risky_amount"])
    risky_lookback_days = int(t["risky_lookback_days"])
    conditions = ["1=1"]
    scope_conds = _silent_scope_clause(agent_ids, sales_names)
    conditions.extend(scope_conds)
    params = []
    params.extend(agent_ids or [])
    params.extend(sales_names or [])

    risky_condition = f"""
        (report_date() - CAST(m.first_bound_time AS DATE) > {risky_days}
         AND s.total_volume < {risky_amount}
         AND NOT EXISTS (
             SELECT 1 FROM transaction_amounts ta
             WHERE ta.merchant_id = m.id
               AND ta.trans_date >= report_cutoff() - INTERVAL {risky_lookback_days} DAY
               AND ta.amount > 0
         ))
    """

    # 类型过滤：与 summary 使用完全相同的高危口径
    if merchant_type == "sleeping":
        conditions.append(f"report_date() - CAST(m.first_bound_time AS DATE) BETWEEN {silent_days} AND {silent_max_days} AND s.total_volume < {silent_amount}")
    elif merchant_type == "risky":
        conditions.append(risky_condition)
    else:
        conditions.append(f"""
            ((report_date() - CAST(m.first_bound_time AS DATE) BETWEEN {silent_days} AND {silent_max_days} AND s.total_volume < {silent_amount})
             OR {risky_condition})
        """)

    if keyword:
        conditions.append("(CAST(m.id AS VARCHAR) LIKE ? OR s.merchant_name LIKE ? OR m.short_name LIKE ? OR m.name LIKE ?)")
        params.extend([f"%{keyword}%"] * 4)

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    # 总数
    total = duckdb_client.query_one(
        f"""
        SELECT COUNT(*) as cnt FROM merchants m
        JOIN ads_merchant_status_snapshots s ON s.merchant_id = m.id
            AND s.snapshot_date = (
                SELECT MAX(snapshot_date) FROM ads_merchant_status_snapshots
                WHERE CAST(snapshot_date AS DATE) <= report_date()
            )
        WHERE {MERCHANT_BINDING_WHERE_M}
          AND m.first_bound_time IS NOT NULL
          AND report_date() - CAST(m.first_bound_time AS DATE) >= {silent_days}
          AND {where}
        """,
        params,
    ).get("cnt", 0)

    # 列表
    rows = duckdb_client.query_all(
        f"""
        SELECT
            m.id AS merchant_id,
            COALESCE(m.short_name, m.name, CAST(m.id AS VARCHAR)) AS merchant_name,
            m.agent_name,
            report_date() - CAST(m.first_bound_time AS DATE) AS binding_days,
            s.total_volume AS history_amount,
            s.last_trans_date,
            s.status_tag,
            COALESCE(
                (SELECT SUM(ta.amount) FROM transaction_amounts ta
                 WHERE ta.merchant_id = m.id AND ta.trans_date >= report_cutoff() - INTERVAL 30 DAY),
                0
            ) AS recent_30d_amount,
            CASE
                WHEN report_date() - CAST(m.first_bound_time AS DATE) BETWEEN {silent_days} AND {silent_max_days} AND s.total_volume < {silent_amount} THEN 'sleeping'
                WHEN report_date() - CAST(m.first_bound_time AS DATE) > {risky_days} AND s.total_volume < {risky_amount}
                     AND NOT EXISTS (
                         SELECT 1 FROM transaction_amounts ta
                         WHERE ta.merchant_id = m.id
                           AND ta.trans_date >= report_cutoff() - INTERVAL {risky_lookback_days} DAY
                           AND ta.amount > 0
                     ) THEN 'risky'
            END AS merchant_type
        FROM merchants m
        JOIN ads_merchant_status_snapshots s ON s.merchant_id = m.id
            AND s.snapshot_date = (
                SELECT MAX(snapshot_date) FROM ads_merchant_status_snapshots
                WHERE CAST(snapshot_date AS DATE) <= report_date()
            )
        WHERE {MERCHANT_BINDING_WHERE_M}
          AND m.first_bound_time IS NOT NULL
          AND report_date() - CAST(m.first_bound_time AS DATE) >= {silent_days}
          AND {where}
        ORDER BY binding_days DESC, m.id DESC
        LIMIT ? OFFSET ?
        """,
        params + [page_size, offset],
    )

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < total,
        "items": rows,
    }
