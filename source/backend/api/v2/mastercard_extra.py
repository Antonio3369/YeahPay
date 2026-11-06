"""
v2 万事达接口 - 按商户排名列表
"""
from fastapi import APIRouter, Depends, Query
from core import duckdb_client, sqlite_client
from core.auth import get_current_user
from api.v2.scope_helper import build_scope_filter
from core.sql_constants import MERCHANT_BINDING_WHERE_M

router = APIRouter(prefix="/v2/mastercard", tags=["v2-万事达"])


@router.get("/rank")
def mastercard_rank(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    keyword: str = Query(None, description="商戶名稱/簡稱/ID"),
    _=Depends(get_current_user),
    scope: tuple = Depends(build_scope_filter),
):
    """万事达按商户累计排名"""
    where, params = scope
    params = list(params)
    if keyword:
        where = f"({where}) AND (CAST(merchant_id AS VARCHAR) LIKE ? OR COALESCE(merchant_short_name, '') LIKE ?)"
        params.extend([f"%{keyword}%"] * 2)

    # 从 thresholds 表取标黄/标红阈值
    thresholds = sqlite_client.query_all("SELECT threshold_key, value, color_warning, color_critical FROM thresholds")
    t_map = {t["threshold_key"]: t for t in thresholds}
    mc = t_map.get("mastercard_amount", {})
    min_amount = float(mc.get("value", 100000))
    warning = float(mc.get("color_warning", 50000))
    critical = float(mc.get("color_critical", 100000))

    offset = (page - 1) * page_size

    aggregate = duckdb_client.query_one(
        f"""
        WITH scoped AS (SELECT * FROM ods_transactions_all WHERE {where}), totals AS (
          SELECT s.merchant_id, SUM(s.amount) AS total_amt
          FROM scoped s JOIN merchants m ON CAST(m.id AS VARCHAR)=CAST(s.merchant_id AS VARCHAR)
          WHERE s.pay_wallet IN ('MASTER', 'MASTER_CARD', 'Mastercard', 'MASTERCARD')
            AND s.trans_status = '交易成功' AND COALESCE(s.amount,0)>0
            AND s.created_at < report_cutoff() AND {MERCHANT_BINDING_WHERE_M}
          GROUP BY s.merchant_id HAVING SUM(s.amount) >= ?
        )
        SELECT COUNT(*) AS cnt,
               COUNT(CASE WHEN total_amt >= ? THEN 1 END) AS warning_count,
               COUNT(CASE WHEN total_amt >= ? THEN 1 END) AS critical_count
        FROM totals
        """,
        params + [min_amount, warning, critical],
    )
    total = aggregate.get("cnt", 0) or 0

    rows = duckdb_client.query_all(
        f"""
        WITH scoped AS (SELECT * FROM ods_transactions_all WHERE {where})
        SELECT
            s.merchant_id,
            MAX(s.merchant_short_name) AS merchant_name,
            COALESCE(SUM(s.amount), 0) AS total_amount,
            COUNT(*) AS txn_count,
            MAX(s.created_at) AS last_trans_time
        FROM scoped s JOIN merchants m ON CAST(m.id AS VARCHAR)=CAST(s.merchant_id AS VARCHAR)
        WHERE s.pay_wallet IN ('MASTER', 'MASTER_CARD', 'Mastercard', 'MASTERCARD')
          AND s.trans_status = '交易成功' AND COALESCE(s.amount,0)>0
          AND s.created_at < report_cutoff() AND {MERCHANT_BINDING_WHERE_M}
        GROUP BY s.merchant_id
        HAVING SUM(s.amount) >= ?
            ORDER BY total_amount DESC, s.merchant_id DESC
        LIMIT ? OFFSET ?
        """,
        params + [min_amount, page_size, offset],
    )

    # 标记预警级别
    for r in rows:
        amt = r["total_amount"]
        if amt >= critical:
            r["level"] = "critical"
        elif amt >= warning:
            r["level"] = "warning"
        else:
            r["level"] = "normal"

    return {
        "items": rows,
        "total": total,
        "warning_threshold": warning,
        "critical_threshold": critical,
        "warning_count": aggregate.get("warning_count", 0) or 0,
        "critical_count": aggregate.get("critical_count", 0) or 0,
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < total,
    }
