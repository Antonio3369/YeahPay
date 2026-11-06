"""v2 实体详情接口"""
from fastapi import APIRouter, Depends, HTTPException, Query
from core import duckdb_client
from core.auth import get_current_user, resolve_scope_values
from api.scope import ensure_merchant_access
from core.sql_constants import merchant_binding_where, successful_transaction_where

router = APIRouter(prefix="/v2/entity", tags=["v2-实体详情"])
HW = merchant_binding_where("m")

def _breakdown(where, params):
    success_tail = f" AND t.created_at < report_cutoff() AND {successful_transaction_where('t')}"
    w = duckdb_client.query_all(f"SELECT t.pay_wallet as name, COUNT(*) as cnt, SUM(t.amount) as volume FROM ods_transactions_all t {where}{success_tail} GROUP BY 1 ORDER BY volume DESC", params)
    l = duckdb_client.query_all(f"SELECT t.card_location as name, COUNT(*) as cnt, SUM(t.amount) as volume FROM ods_transactions_all t {where}{success_tail} GROUP BY 1 ORDER BY volume DESC", params)
    s = duckdb_client.query_all(f"SELECT t.trans_status as name, COUNT(*) as cnt, SUM(ABS(t.amount)) as volume FROM ods_transactions_all t {where} AND t.created_at < report_cutoff() GROUP BY 1 ORDER BY volume DESC", params)
    return {"wallet": w, "location": l, "status": s}


def _ensure_named_entity_access(name: str, current_user: dict):
    if current_user["role"] == "admin":
        return
    agent_ids, sales_names = resolve_scope_values(current_user)
    if name in sales_names:
        return
    if agent_ids:
        placeholders = ",".join("?" for _ in agent_ids)
        belongs = duckdb_client.query_one(
            "SELECT 1 AS ok FROM merchants m JOIN merchant_trans_30 mt ON CAST(mt.商户编号 AS VARCHAR)=CAST(m.id AS VARCHAR) "
            f"WHERE CAST(m.agent_id AS VARCHAR) IN ({placeholders}) AND mt.业务员=? AND {HW} LIMIT 1",
            [*agent_ids, name],
        )
        if belongs:
            return
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="无权访问该销售数据")


def _ensure_agent_access(requested_agent_id: str, current_user: dict):
    if current_user["role"] == "admin":
        return
    agent_ids, _ = resolve_scope_values(current_user)
    if not agent_ids or str(requested_agent_id) not in {str(v) for v in agent_ids}:
        raise HTTPException(status_code=403, detail="无权访问代理商数据")

@router.get("/agent/{agent_id}")
def agent(agent_id: str, current_user=Depends(get_current_user)):
    _ensure_agent_access(agent_id, current_user)
    r = duckdb_client.query_one(f"SELECT agent_id, agent_name, COUNT(*) as mc FROM merchants m WHERE m.status!='注销' AND {HW} AND CAST(m.agent_id AS VARCHAR)=? GROUP BY 1,2", [agent_id])
    if not r or not r.get("agent_name"): raise HTTPException(404, "不存在")
    stats = duckdb_client.query_one(f"""
        SELECT COALESCE(SUM(t.amount), 0) AS vol,
               COUNT(DISTINCT t.merchant_id) AS active_count
        FROM ods_transactions_all t
        JOIN merchants m ON CAST(m.id AS VARCHAR)=CAST(t.merchant_id AS VARCHAR)
        WHERE t.created_at>=DATE_TRUNC('month',report_date())
          AND t.created_at< report_cutoff()
          AND {successful_transaction_where('t')}
          AND m.status!='注销' AND {HW}
          AND CAST(m.agent_id AS VARCHAR)=?
    """, [agent_id])
    return {"agent_id": r["agent_id"], "agent_name": r["agent_name"], "merchant_count": r["mc"], "active_count": stats.get("active_count",0) or 0, "this_month_volume": (stats.get("vol",0) or 0)}

@router.get("/agent/{agent_id}/breakdown")
def agent_bd(agent_id: str, current_user=Depends(get_current_user)):
    _ensure_agent_access(agent_id, current_user)
    return _breakdown(f"JOIN merchants m ON CAST(m.id AS VARCHAR)=CAST(t.merchant_id AS VARCHAR) WHERE CAST(m.agent_id AS VARCHAR)=? AND t.created_at>=DATE_TRUNC('month', report_date())-INTERVAL 2 MONTH AND m.status!='注销' AND {HW}", [agent_id])

@router.get("/sales/{sales_name}")
def sales(sales_name: str, current_user=Depends(get_current_user)):
    _ensure_named_entity_access(sales_name, current_user)
    import calendar
    report_date = duckdb_client.get_reporting_date()
    previous_month = 12 if report_date.month == 1 else report_date.month - 1
    previous_year = report_date.year - 1 if report_date.month == 1 else report_date.year
    compare_days = min(report_date.day, calendar.monthrange(previous_year, previous_month)[1])
    r = duckdb_client.query_one(f"""
        WITH sales_merchants AS (
          SELECT DISTINCT CAST(mt.商户编号 AS VARCHAR) AS merchant_id
          FROM merchant_trans_30 mt WHERE mt.业务员=?
        )
        SELECT ? AS sn, COUNT(DISTINCT m.id) AS mc,
               COUNT(DISTINCT CASE WHEN m.first_bound_time IS NOT NULL
                                      AND m.first_bound_time < DATE_TRUNC('month', report_date())
                                    THEN m.id END) AS prev_mc,
               COUNT(DISTINCT CASE WHEN t.created_at>=DATE_TRUNC('month',report_date())
                    AND t.created_at< report_cutoff() AND {successful_transaction_where('t')}
                    THEN m.id END) AS ac,
               COUNT(DISTINCT CASE WHEN t.created_at>=DATE_TRUNC('month', report_date()) - INTERVAL 1 MONTH
                                      AND t.created_at<DATE_TRUNC('month', report_date()) - INTERVAL 1 MONTH
                                          + INTERVAL '{compare_days}' DAY
                                      AND {successful_transaction_where('t')}
                                    THEN m.id END) AS prev_ac,
               COALESCE(SUM(CASE WHEN t.created_at>=DATE_TRUNC('month',report_date())
                    AND t.created_at< report_cutoff() AND {successful_transaction_where('t')}
                    THEN t.amount ELSE 0 END),0) AS tv
        FROM sales_merchants sm
        JOIN merchants m ON CAST(m.id AS VARCHAR)=sm.merchant_id
        LEFT JOIN ods_transactions_all t ON CAST(t.merchant_id AS VARCHAR)=sm.merchant_id
        WHERE m.status!='注销' AND {HW}
    """, [sales_name, sales_name])
    if not r or not r.get("sn"): raise HTTPException(404, "不存在")
    return {
        "sales_name": r["sn"],
        "merchant_count": r["mc"],
        "previous_merchant_count": r["prev_mc"] or 0,
        "active_count": r["ac"] or 0,
        "previous_active_count": r["prev_ac"] or 0,
        "this_month_vol": r["tv"] or 0,
    }

@router.get("/sales/{sales_name}/breakdown")
def sales_bd(sales_name: str, current_user=Depends(get_current_user)):
    _ensure_named_entity_access(sales_name, current_user)
    return _breakdown(f"JOIN merchant_trans_30 mt ON CAST(mt.商户编号 AS VARCHAR)=CAST(t.merchant_id AS VARCHAR) JOIN merchants m ON CAST(m.id AS VARCHAR)=CAST(t.merchant_id AS VARCHAR) WHERE mt.业务员=? AND t.created_at>=DATE_TRUNC('month', report_date())-INTERVAL 2 MONTH AND m.status!='注销' AND {HW}", [sales_name])

@router.get("/sales/{sales_name}/merchants")
def sales_m(
    sales_name: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user=Depends(get_current_user),
):
    _ensure_named_entity_access(sales_name, current_user)
    where = f"mt.业务员=? AND m.status!='注销' AND {HW}"
    count = duckdb_client.query_one(
        f"SELECT COUNT(DISTINCT m.id) AS cnt FROM merchants m JOIN merchant_trans_30 mt ON CAST(mt.商户编号 AS VARCHAR)=CAST(m.id AS VARCHAR) WHERE {where}",
        [sales_name],
    ).get("cnt", 0)
    offset = (page - 1) * page_size
    rows = duckdb_client.query_all(
        f"SELECT m.id, m.short_name, m.name, COALESCE(SUM(CASE WHEN t.created_at>=DATE_TRUNC('month',report_date()) AND t.created_at< report_cutoff() AND {successful_transaction_where('t')} THEN t.amount ELSE 0 END),0) as tv FROM merchants m JOIN merchant_trans_30 mt ON CAST(mt.商户编号 AS VARCHAR)=CAST(m.id AS VARCHAR) LEFT JOIN ods_transactions_all t ON CAST(t.merchant_id AS VARCHAR)=CAST(m.id AS VARCHAR) WHERE {where} GROUP BY m.id,m.short_name,m.name ORDER BY tv DESC, m.id DESC LIMIT ? OFFSET ?",
        [sales_name, page_size, offset],
    )
    return {"items": rows, "total": count, "page": page, "page_size": page_size, "has_more": page * page_size < count}

@router.get("/merchant/{merchant_id}/breakdown")
def mch_bd(merchant_id: str, current_user=Depends(get_current_user)):
    ensure_merchant_access(merchant_id, current_user)
    return _breakdown(f"WHERE CAST(t.merchant_id AS VARCHAR)=? AND t.created_at>=DATE_TRUNC('month', report_date())-INTERVAL 2 MONTH", [merchant_id])

@router.get("/merchant/{merchant_id}/info")
def mch_info(merchant_id: str, current_user=Depends(get_current_user)):
    ensure_merchant_access(merchant_id, current_user)
    r = duckdb_client.query_one(f"SELECT id, name, short_name, agent_id, agent_name, bound_sn, bound_qr_code, first_bound_time, status FROM merchants m WHERE CAST(m.id AS VARCHAR)=? AND m.status!='注销' AND {HW}", [merchant_id])
    if not r: raise HTTPException(404, "不存在")
    s = duckdb_client.query_one("SELECT 业务员 FROM merchant_trans_30 WHERE CAST(商户编号 AS VARCHAR)=? AND 业务员 IS NOT NULL AND 业务员!=''", [merchant_id])
    d = dict(r)
    d["sales_name"] = s.get("业务员") if s else None
    d["first_bound_time"] = str(d.get("first_bound_time",""))[:10] if d.get("first_bound_time") else None
    return d

@router.get("/agent/{agent_id}/trend")
def agent_trend(agent_id: str, current_user=Depends(get_current_user)):
    _ensure_agent_access(agent_id, current_user)
    """代理商月度新增商户+交易额趋势"""
    return duckdb_client.query_all(f"""
        SELECT DATE_TRUNC('MONTH', CAST(m.first_bound_time AS DATE)) AS month,
               COUNT(DISTINCT m.id) AS new_merchants,
               COALESCE(SUM(t.amount), 0) AS new_volume
        FROM merchants m
        LEFT JOIN ods_transactions_all t ON CAST(t.merchant_id AS VARCHAR) = CAST(m.id AS VARCHAR)
            AND t.created_at >= CAST(m.first_bound_time AS DATE)
            AND t.created_at < CAST(m.first_bound_time AS DATE) + INTERVAL 30 DAY
            AND t.created_at < report_cutoff()
            AND {successful_transaction_where('t')}
        JOIN merchant_trans_30 mt ON CAST(mt.商户编号 AS VARCHAR) = CAST(m.id AS VARCHAR)
        WHERE CAST(m.agent_id AS VARCHAR) = ?
          AND m.first_bound_time IS NOT NULL
            AND m.first_bound_time >= DATE_TRUNC('month', report_date()) - INTERVAL 11 MONTH
          AND m.first_bound_time < report_cutoff()
          AND m.status != '注销'
          AND {HW}
          AND mt.业务员 IS NOT NULL AND mt.业务员 != ''
        GROUP BY 1 ORDER BY 1
    """, [agent_id])
