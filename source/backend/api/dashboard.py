"""
工作台模块 - 截图 B/C/D
- B: 今日交易概览（笔数/金额/成功率）
- C: 近 30 天交易趋势
- D: 商户/通道分布
所有接口支持视角切换（agent_id / sales_person）
"""
from fastapi import APIRouter, Depends, Query
from core import duckdb_client
from core.auth import get_current_user
from api.scope import build_scope_filter

router = APIRouter(prefix="/dashboard", tags=["工作台"])


@router.get("/overview")
def overview(
    days: int = Query(1, description="最近N天，默认今日"),
    _=Depends(get_current_user),
    scope: tuple = Depends(build_scope_filter),
):
    """B - 交易概览（今日/近N天）"""
    where, params = scope
    sql = f"""
        SELECT
            COUNT(*)                    AS txn_count,
            COALESCE(SUM(amount), 0)    AS total_amount,
            COUNT(DISTINCT merchant_id) AS merchant_count,
            COUNT(CASE WHEN trans_status = '交易成功' THEN 1 END) AS success_count,
            COUNT(CASE WHEN trans_status = '交易失败' THEN 1 END) AS fail_count,
            COUNT(CASE WHEN trans_status = '退款成功' THEN 1 END) AS refund_count
        FROM ods_transactions_all
        WHERE created_at >= CURRENT_DATE - INTERVAL '{days}' DAY
          AND {where}
    """
    row = duckdb_client.query_one(sql, params)
    total = row["txn_count"] or 1
    row["success_rate"] = round(row["success_count"] / total * 100, 2) if total else 0
    row["fail_rate"] = round(row["fail_count"] / total * 100, 2) if total else 0
    return row


@router.get("/trend")
def trend(
    days: int = Query(30, description="趋势天数"),
    _=Depends(get_current_user),
    scope: tuple = Depends(build_scope_filter),
):
    """C - 近N天交易趋势（折线图）"""
    where, params = scope
    sql = f"""
        SELECT
            CAST(created_at AS DATE)                                AS date,
            COUNT(*)                                                AS txn_count,
            COALESCE(SUM(amount), 0)                                AS total_amount,
            COUNT(CASE WHEN trans_status = '交易成功' THEN 1 END)   AS success_count
        FROM ods_transactions_all
        WHERE created_at >= CURRENT_DATE - INTERVAL '{days}' DAY
          AND {where}
        GROUP BY 1
        ORDER BY 1
    """
    return duckdb_client.query_all(sql, params)


@router.get("/distribution")
def distribution(
    days: int = Query(30),
    _=Depends(get_current_user),
    scope: tuple = Depends(build_scope_filter),
):
    """D - 分布（按交易类型/通道/钱包）"""
    where, params = scope
    sql = f"""
        SELECT
            trans_type,
            trans_channel,
            pay_wallet,
            COUNT(*)                 AS txn_count,
            COALESCE(SUM(amount), 0) AS total_amount
        FROM ods_transactions_all
        WHERE created_at >= CURRENT_DATE - INTERVAL '{days}' DAY
          AND {where}
        GROUP BY 1, 2, 3
        ORDER BY total_amount DESC, trans_type, trans_channel, pay_wallet
    """
    return duckdb_client.query_all(sql, params)
