"""
沉默商户模块 - 新增页面
- 沉睡商户: 绑定 14-180 天无交易
- 风险商户: 绑定 >180 天无交易（建议回收）
数据来源: ads_merchant_status_snapshots + transaction_amounts
"""
from fastapi import APIRouter, Depends, Query
from core import duckdb_client
from core.auth import get_current_user
from api.scope import build_scope_filter

router = APIRouter(prefix="/silent", tags=["沉默商户"])


@router.get("/summary")
def summary(scope: tuple = Depends(build_scope_filter)):
    """沉默商户统计概览"""
    where, params = scope
    sql = f"""
        SELECT
            CASE 
                WHEN binding_days BETWEEN 14 AND 180 THEN 'sleeping'
                WHEN binding_days > 180 THEN 'risky'
            END AS merchant_type,
            COUNT(*) AS count,
            COALESCE(SUM(total_volume), 0) AS total_history_amount
        FROM ads_merchant_status_snapshots
        WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM ads_merchant_status_snapshots)
          AND binding_days >= 14
          AND {where}
        GROUP BY 1
    """
    rows = duckdb_client.query_all(sql, params)
    result = {"sleeping": 0, "risky": 0, "sleeping_amount": 0, "risky_amount": 0}
    for r in rows:
        if r["merchant_type"] == "sleeping":
            result["sleeping"] = r["count"]
            result["sleeping_amount"] = r["total_history_amount"]
        else:
            result["risky"] = r["count"]
            result["risky_amount"] = r["total_history_amount"]
    result["total"] = result["sleeping"] + result["risky"]
    return result


@router.get("/list")
def list_silent(
    merchant_type: str = Query("all", description="all/sleeping/risky"),
    keyword: str = Query(None, description="商户名/编号搜索"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    scope: tuple = Depends(build_scope_filter),
):
    """沉默商户列表"""
    scope_where, scope_params = scope
    conditions = ["s.binding_days >= 14", scope_where]
    params = list(scope_params)

    if merchant_type == "sleeping":
        conditions.append("s.binding_days BETWEEN 14 AND 180")
    elif merchant_type == "risky":
        conditions.append("s.binding_days > 180")

    if keyword:
        conditions.append("(CAST(s.merchant_id AS VARCHAR) LIKE ? OR s.merchant_name LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    # 总数
    total = duckdb_client.query_one(
        f"""
        SELECT COUNT(*) as cnt
        FROM ads_merchant_status_snapshots s
        WHERE s.snapshot_date = (SELECT MAX(snapshot_date) FROM ads_merchant_status_snapshots)
          AND {where}
        """,
        params,
    )["cnt"]

    # 列表（关联近30日交易额）
    rows = duckdb_client.query_all(
        f"""
        SELECT
            s.merchant_id,
            s.merchant_name,
            s.agent_name,
            s.binding_days,
            s.total_volume AS history_amount,
            s.last_trans_date,
            s.status_tag,
            COALESCE(
                (SELECT SUM(t.amount) FROM transaction_amounts t 
                 WHERE t.merchant_id = s.merchant_id 
                   AND t.trans_date >= CURRENT_DATE - INTERVAL 30 DAY),
                0
            ) AS recent_30d_amount,
            CASE 
                WHEN s.binding_days BETWEEN 14 AND 180 THEN 'sleeping'
                WHEN s.binding_days > 180 THEN 'risky'
            END AS merchant_type
        FROM ads_merchant_status_snapshots s
        WHERE s.snapshot_date = (SELECT MAX(snapshot_date) FROM ads_merchant_status_snapshots)
          AND {where}
        ORDER BY s.binding_days DESC
        LIMIT ? OFFSET ?
        """,
        params + [page_size, offset],
    )

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": rows,
    }
