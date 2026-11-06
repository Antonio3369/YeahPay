"""
v2 境外卡接口 - 完整版（含占本月%和合计）
"""
from fastapi import APIRouter, Depends, Query
from core import duckdb_client
from core.auth import get_current_user
from api.v2.scope_helper import build_scope_filter
from core.sql_constants import MERCHANT_BINDING_WHERE_M, successful_transaction_where

router = APIRouter(prefix="/v2/overseas", tags=["v2-境外卡"])


@router.get("/top10")
def overseas_top10(
    days: int = Query(30, ge=1, le=365),
    keyword: str = Query(None, description="商戶名稱/簡稱/ID"),
    _=Depends(get_current_user),
    scope: tuple = Depends(build_scope_filter),
):
    """境外卡 Top10 - 完整版"""
    where, params = scope
    params = list(params)
    if keyword:
        where = f"({where}) AND (CAST(merchant_id AS VARCHAR) LIKE ? OR COALESCE(merchant_short_name, '') LIKE ?)"
        params.extend([f"%{keyword}%"] * 2)
    sql = f"""
        WITH scoped AS (
            SELECT * FROM ods_transactions_all WHERE {where}
        ), eligible AS (
            SELECT s.* FROM scoped s
            JOIN merchants m ON CAST(m.id AS VARCHAR)=CAST(s.merchant_id AS VARCHAR)
            WHERE {MERCHANT_BINDING_WHERE_M}
        ), overseas AS (
            SELECT
                merchant_id,
                MAX(merchant_short_name) AS merchant_name,
                COUNT(*) AS txn_count,
                COALESCE(SUM(amount), 0) AS overseas_amount
            FROM eligible
            WHERE card_location = '外地'
              AND trans_status = '交易成功'
              AND COALESCE(amount, 0) > 0
              AND created_at >= report_cutoff() - INTERVAL '{days}' DAY
              AND created_at < report_cutoff()
            GROUP BY merchant_id
        ),
        period_total AS (
            SELECT
                merchant_id,
                COALESCE(SUM(amount), 0) AS period_amount
            FROM eligible
            WHERE trans_status = '交易成功'
              AND COALESCE(amount, 0) > 0
              AND created_at >= report_cutoff() - INTERVAL '{days}' DAY
              AND created_at < report_cutoff()
            GROUP BY merchant_id
        )
        SELECT
            o.merchant_id,
            o.merchant_name,
            o.overseas_amount,
            o.txn_count,
            COALESCE(p.period_amount, 0) AS month_total,
            CASE WHEN p.period_amount > 0 THEN ROUND(o.overseas_amount * 100.0 / p.period_amount, 1) ELSE 0 END AS pct_of_month,
            SUM(o.overseas_amount) OVER () AS all_overseas_amount
        FROM overseas o
        LEFT JOIN period_total p ON p.merchant_id = o.merchant_id
        ORDER BY o.overseas_amount DESC, CAST(o.merchant_id AS VARCHAR) DESC
        LIMIT 10
    """
    rows = duckdb_client.query_all(sql, params)
    total_amount = rows[0].get("all_overseas_amount", 0) if rows else 0
    for row in rows:
        row.pop("all_overseas_amount", None)
    return {"items": rows, "total_amount": total_amount}
