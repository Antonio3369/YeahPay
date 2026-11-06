"""
境外卡 Top10 模块 - 截图 K
支持视角切换
"""
from fastapi import APIRouter, Depends, Query
from core import duckdb_client
from core.auth import get_current_user
from api.scope import build_scope_filter

router = APIRouter(prefix="/overseas", tags=["境外卡"])


@router.get("/top10")
def overseas_top10(
    days: int = Query(30),
    _=Depends(get_current_user),
    scope: tuple = Depends(build_scope_filter),
):
    """K - 境外卡交易额 Top10 商户"""
    where, params = scope
    sql = f"""
        SELECT
            merchant_id,
            MAX(merchant_short_name) AS merchant_name,
            COUNT(*)                  AS txn_count,
            COALESCE(SUM(amount), 0)  AS total_amount,
            COUNT(*) * 100.0 / NULLIF(
                SUM(COUNT(*)) OVER (),
                0
            )                         AS pct
        FROM ods_transactions_all
        WHERE card_location = '外地'
          AND trans_status = '交易成功'
          AND created_at >= CURRENT_DATE - INTERVAL '{days} DAY'
          AND {where}
        GROUP BY merchant_id
        ORDER BY total_amount DESC, CAST(merchant_id AS VARCHAR) DESC
        LIMIT 10
    """
    return duckdb_client.query_all(sql, params)
