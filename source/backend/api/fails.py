"""
交易失败模块 - 截图 J
数据来源: ods_transactions_all filter trans_status='交易失败'
支持视角切换
"""
from fastapi import APIRouter, Depends, Query
from core import duckdb_client
from core.auth import get_current_user
from api.scope import build_scope_filter
from core.sql_constants import MERCHANT_BINDING_WHERE_M

router = APIRouter(prefix="/fails", tags=["交易失败"])


@router.get("/summary")
def fail_summary(
    days: int = Query(30, ge=1, le=365, description="最近N天"),
    _=Depends(get_current_user),
    scope: tuple = Depends(build_scope_filter),
):
    """J - 失败笔数/金额/最近失败时间（按商户聚合）"""
    where, params = scope
    sql = f"""
        SELECT
            merchant_id,
            MAX(merchant_short_name) AS merchant_name,
            COUNT(*)                  AS fail_count,
            COALESCE(SUM(amount), 0)  AS fail_amount,
            MAX(created_at)           AS last_fail_time
        FROM ods_transactions_all
        WHERE trans_status = '交易失败'
          AND created_at >= report_cutoff() - INTERVAL '{days} DAY'
          AND created_at < report_cutoff()
          AND EXISTS (
              SELECT 1 FROM merchants m
              WHERE CAST(m.id AS VARCHAR)=CAST(ods_transactions_all.merchant_id AS VARCHAR)
                AND {MERCHANT_BINDING_WHERE_M}
          )
          AND {where}
        GROUP BY merchant_id
        ORDER BY fail_count DESC, CAST(merchant_id AS VARCHAR) DESC
        LIMIT 10
    """
    return duckdb_client.query_all(sql, params)


@router.get("/detail")
def fail_detail(
    merchant_id: str = Query(None),
    days: int = Query(30, ge=1, le=365, description="与汇总相同的最近N天"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _=Depends(get_current_user),
    scope: tuple = Depends(build_scope_filter),
):
    """J - 失败订单明细"""
    scope_where, scope_params = scope
    conditions = [
        "trans_status = '交易失败'",
        f"created_at >= report_cutoff() - INTERVAL '{days} DAY'",
        "created_at < report_cutoff()",
        f"EXISTS (SELECT 1 FROM merchants m WHERE CAST(m.id AS VARCHAR)=CAST(ods_transactions_all.merchant_id AS VARCHAR) AND {MERCHANT_BINDING_WHERE_M})",
        scope_where,
    ]
    params = list(scope_params)
    if merchant_id:
        conditions.append("CAST(merchant_id AS VARCHAR) = ?")
        params.append(merchant_id)

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    total = duckdb_client.query_one(
        f"SELECT COUNT(*) FROM ods_transactions_all WHERE {where}", params
    )["count_star()"]

    rows = duckdb_client.query_all(
        f"""
        SELECT order_no, created_at, merchant_id, merchant_short_name,
               amount, trans_channel, pay_wallet, error_code, error_msg
        FROM ods_transactions_all
        WHERE {where}
        ORDER BY created_at DESC, order_no DESC
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
