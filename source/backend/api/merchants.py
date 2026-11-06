"""
商户模块 - 截图 F/G/H
- F: 商户列表（搜索/筛选/分页）
- G: 商户详情（基础信息 + 环比 + 额度）
- H: 交易明细（按商户）
支持视角切换（agent_id / sales_person）
"""
from fastapi import APIRouter, Depends, Query, HTTPException
from core import duckdb_client
from core.auth import get_current_user, resolve_scope_values
from api.scope import ensure_merchant_access
from core.sql_constants import MERCHANT_BINDING_WHERE_M, successful_transaction_where

router = APIRouter(prefix="/merchants", tags=["商户"])


def build_merchant_scope(agent_id: str = None, sales_person: str = None, current_user: dict = None):
    """构建商户表的视角过滤条件（merchants 表用 agent_id，销售用 merchant_trans_30）"""
    conditions = ["1=1"]
    params = []

    if current_user:
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

    return " AND ".join(conditions), params


@router.get("")
def list_merchants(
    keyword: str = Query(None, description="商户名/编号搜索"),
    agent_id: str = Query(None, description="代理商ID（视角切换）"),
    sales_person: str = Query(None, description="销售名（视角切换）"),
    status: str = Query(None, description="状态: 正常/注销"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user=Depends(get_current_user),
):
    """F - 商户列表"""
    scope_where, scope_params = build_merchant_scope(agent_id, sales_person, current_user)
    conditions = [scope_where]
    params = list(scope_params)

    if keyword:
        conditions.append("(CAST(m.id AS VARCHAR) LIKE ? OR m.name LIKE ? OR m.short_name LIKE ?)")
        params.extend([f"%{keyword}%"] * 3)
    if status:
        conditions.append("m.status = ?")
        params.append(status)

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    count_sql = f"SELECT COUNT(*) FROM merchants m WHERE {where} AND {MERCHANT_BINDING_WHERE_M}"
    total = duckdb_client.query_one(count_sql, params)["count_star()"]

    list_sql = f"""
        SELECT
            m.id, m.name, m.short_name, m.agent_name, m.status,
            m.bound_sn, m.bound_qr_code, m.first_bound_time, m.last_trans_time,
            COALESCE(SUM(t.amount), 0) AS recent_30d_amount,
            COUNT(t.order_no) AS recent_30d_count
        FROM merchants m
        LEFT JOIN ods_transactions_all t
            ON CAST(t.merchant_id AS VARCHAR) = CAST(m.id AS VARCHAR)
            AND t.created_at >= report_cutoff() - INTERVAL 30 DAY
            AND t.created_at < report_cutoff()
            AND {successful_transaction_where('t')}
        WHERE {where} AND {MERCHANT_BINDING_WHERE_M}
        GROUP BY m.id, m.name, m.short_name, m.agent_name, m.status,
                 m.bound_sn, m.bound_qr_code, m.first_bound_time, m.last_trans_time
        ORDER BY recent_30d_amount DESC, m.id DESC
        LIMIT ? OFFSET ?
    """
    rows = duckdb_client.query_all(list_sql, params + [page_size, offset])

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": rows,
    }


@router.get("/{merchant_id}")
def get_merchant(merchant_id: str, current_user=Depends(get_current_user)):
    """G - 商户详情"""
    ensure_merchant_access(merchant_id, current_user)
    merchant = duckdb_client.query_one(
        f"SELECT m.* FROM merchants m WHERE CAST(m.id AS VARCHAR) = ? AND {MERCHANT_BINDING_WHERE_M}",
        [merchant_id],
    )
    if not merchant:
        raise HTTPException(status_code=404, detail="商户不存在")

    stats = duckdb_client.query_all(
        f"""
        SELECT
            DATE_TRUNC('day', created_at)   AS period,
            SUM(amount)                      AS amount,
            COUNT(*)                         AS count
        FROM ods_transactions_all
        WHERE CAST(merchant_id AS VARCHAR) = ?
          AND created_at >= report_cutoff() - INTERVAL 30 DAY
          AND created_at < report_cutoff()
          AND {successful_transaction_where('ods_transactions_all')}
        GROUP BY 1 ORDER BY 1
        """,
        [merchant_id],
    )

    mtd = duckdb_client.query_one(
        f"""
        SELECT COALESCE(SUM(amount), 0) AS mtd_amount, COUNT(*) AS mtd_count
        FROM ods_transactions_all
        WHERE CAST(merchant_id AS VARCHAR) = ?
          AND created_at >= DATE_TRUNC('month', report_date())
          AND created_at < report_cutoff()
          AND {successful_transaction_where('ods_transactions_all')}
        """,
        [merchant_id],
    )

    return {**merchant, "daily_stats": stats, "mtd": mtd}


@router.get("/{merchant_id}/transactions")
def merchant_transactions(
    merchant_id: str,
    status: str = Query(None),
    trans_type: str = Query(None),
    start_date: str = Query(None),
    end_date: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user=Depends(get_current_user),
):
    """H - 商户交易明细"""
    ensure_merchant_access(merchant_id, current_user)
    conditions = ["CAST(merchant_id AS VARCHAR) = ?"]
    params = [merchant_id]
    if status:
        conditions.append("trans_status = ?")
        params.append(status)
    if trans_type:
        conditions.append("trans_type = ?")
        params.append(trans_type)
    if start_date:
        conditions.append("created_at >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("created_at <= ?")
        params.append(end_date)

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    total = duckdb_client.query_one(f"SELECT COUNT(*) FROM ods_transactions_all WHERE {where}", params)["count_star()"]
    rows = duckdb_client.query_all(
        f"""
        SELECT order_no, created_at, amount, trans_type, trans_status,
               trans_channel, pay_wallet, card_location, card_type,
               masked_card_no, error_code, error_msg
        FROM ods_transactions_all
        WHERE {where}
        ORDER BY created_at DESC, order_no DESC
        LIMIT ? OFFSET ?
        """,
        params + [page_size, offset],
    )

    return {"total": total, "page": page, "page_size": page_size, "items": rows}
