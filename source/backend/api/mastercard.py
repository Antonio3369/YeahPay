"""
万事达累计模块 - 截图 L
支持视角切换
"""
from fastapi import APIRouter, Depends, Query
from core import duckdb_client
from core.auth import get_current_user
from api.scope import build_scope_filter

router = APIRouter(prefix="/mastercard", tags=["万事达"])


@router.get("/summary")
def mastercard_summary(
    _=Depends(get_current_user),
    scope: tuple = Depends(build_scope_filter),
):
    """L - 累计 Mastercard 金额/笔数/最后交易"""
    where, params = scope
    sql = f"""
        SELECT
            COALESCE(SUM(amount), 0)  AS total_amount,
            COUNT(*)                  AS total_count,
            MAX(created_at)           AS last_trans_time
        FROM ods_transactions_all
        WHERE pay_wallet IN ('MASTER', 'MASTER_CARD', 'Mastercard', 'MASTERCARD')
          AND trans_status = '交易成功'
          AND {where}
    """
    return duckdb_client.query_one(sql, params)
