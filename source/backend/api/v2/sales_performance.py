"""v2 销售业绩表现 - 增量 + 存量"""
from typing import Literal
from fastapi import APIRouter, Depends, Query
from core import duckdb_client
from core.auth import get_current_user, require_roles, resolve_scope_values
from core.sql_constants import MERCHANT_BINDING_WHERE, merchant_binding_where
from core.sql_constants import successful_transaction_where

router = APIRouter(prefix="/v2/sales-performance", tags=["v2-销售业绩"])

HW = merchant_binding_where("m")


def _scope(agent_ids, sales_names, restricted=False):
    parts = []
    params = []
    if agent_ids:
        placeholders = ",".join("?" for _ in agent_ids)
        parts.append(f"CAST(m.agent_id AS VARCHAR) IN ({placeholders})")
        params.extend(agent_ids)
    if sales_names:
        placeholders = ",".join("?" for _ in sales_names)
        parts.append(f"mt.业务员 IN ({placeholders})")
        params.extend(sales_names)
    if parts:
        return "(" + " OR ".join(parts) + ") AND mt.业务员 IS NOT NULL AND mt.业务员 != '' AND " + HW, params
    return ("1=0" if restricted else "mt.业务员 IS NOT NULL AND mt.业务员 != ''"), []


@router.get("/trend")
def trend(
    months: int = Query(6, ge=1, le=24, description="回溯月数"),
    agent_id: str = Query(None),
    sales_person: str = Query(None),
    current_user=Depends(require_roles("admin", "supervisor")),
):
    """增量：最近 N 个自然月新增商户走势。"""
    window_months = months - 1
    agent_ids, sales_names = resolve_scope_values(current_user, agent_id, sales_person)
    w, p = _scope(agent_ids, sales_names, current_user["role"] != "admin")
    sql = f"""
        SELECT
            DATE_TRUNC('MONTH', CAST(m.first_bound_time AS DATE)) AS month,
            COUNT(DISTINCT m.id) AS new_merchants,
            COALESCE(SUM(t.amount), 0) AS new_volume
        FROM merchants m
        JOIN merchant_trans_30 mt ON CAST(mt.商户编号 AS VARCHAR) = CAST(m.id AS VARCHAR)
        LEFT JOIN ods_transactions_all t ON CAST(t.merchant_id AS VARCHAR) = CAST(m.id AS VARCHAR)
            AND t.created_at >= CAST(m.first_bound_time AS DATE)
            AND t.created_at < CAST(m.first_bound_time AS DATE) + INTERVAL 30 DAY
            AND t.created_at < report_cutoff()
            AND {successful_transaction_where('t')}
        WHERE m.first_bound_time IS NOT NULL
          AND m.first_bound_time >= DATE_TRUNC('MONTH', report_date()) - INTERVAL {window_months} MONTH
          AND m.first_bound_time < report_cutoff()
          AND m.status != '注销' AND {HW}
          AND {w}
        GROUP BY 1 ORDER BY month ASC
    """
    return duckdb_client.query_all(sql, p)


@router.get("/existing-trend")
def existing_trend(
    months: int = Query(6, ge=1, le=24, description="回溯月数"),
    agent_id: str = Query(None),
    sales_person: str = Query(None),
    current_user=Depends(require_roles("admin", "supervisor")),
):
    """存量：近 N 月活跃商户数与交易额走势。

    与 /trend 的增量口径分开：这里按每月实际发生交易的商户统计，
    不使用 first_bound_time，也不把首月交易额混入存量数据。
    """
    agent_ids, sales_names = resolve_scope_values(current_user, agent_id, sales_person)
    w, p = _scope(agent_ids, sales_names, current_user["role"] != "admin")
    window_months = months - 1
    sql = f"""
        WITH monthly_data AS (
            SELECT
                t.merchant_id,
                DATE_TRUNC('MONTH', t.created_at) AS month,
                SUM(t.amount) AS amount
            FROM ods_transactions_all t
            WHERE t.created_at >= DATE_TRUNC('MONTH', report_date()) - INTERVAL {window_months} MONTH
              AND t.created_at < report_cutoff()
              AND {successful_transaction_where('t')}
            GROUP BY 1, 2
        )
        SELECT
            md.month,
            COUNT(DISTINCT md.merchant_id) AS existing_merchants,
            COALESCE(SUM(md.amount), 0) AS existing_volume
        FROM monthly_data md
        JOIN merchant_trans_30 mt
          ON CAST(mt.商户编号 AS VARCHAR) = CAST(md.merchant_id AS VARCHAR)
        JOIN merchants m
          ON CAST(m.id AS VARCHAR) = CAST(md.merchant_id AS VARCHAR)
        WHERE m.status != '注销'
          AND {HW}
          AND {w}
        GROUP BY 1
        ORDER BY 1 ASC
    """
    return duckdb_client.query_all(sql, p)


@router.get("/summary")
def summary(
    data_type: Literal["new", "existing"] = Query("new", description="new(增量) / existing(存量)"),
    agent_id: str = Query(None),
    sales_person: str = Query(None),
    current_user=Depends(require_roles("admin", "supervisor")),
):
    """返回当前月份的全量业绩汇总。

    不能从 ``/rank`` 的 Top20 结果反推全局指标，否则销售数量一多，
    页面卡片会少算。该接口与趋势图使用同一套有效商户、成功交易和发布
    日期口径，前端只用它渲染汇总卡片。
    """
    agent_ids, sales_names = resolve_scope_values(current_user, agent_id, sales_person)
    w, p = _scope(agent_ids, sales_names, current_user["role"] != "admin")

    if data_type == "new":
        sql = f"""
            WITH new_merchants AS (
                SELECT DISTINCT
                    CAST(m.id AS VARCHAR) AS mid,
                    mt.业务员 AS sales_name,
                    CAST(m.first_bound_time AS DATE) AS bind_date
                FROM merchants m
                JOIN merchant_trans_30 mt
                  ON CAST(mt.商户编号 AS VARCHAR) = CAST(m.id AS VARCHAR)
                WHERE m.first_bound_time IS NOT NULL
                  AND m.first_bound_time >= DATE_TRUNC('MONTH', report_date())
                  AND m.first_bound_time < report_cutoff()
                  AND m.status != '注销' AND {HW}
                  AND {w}
            ), first_30d AS (
                SELECT
                    nm.mid,
                    nm.sales_name,
                    COALESCE(SUM(t.amount), 0) AS volume
                FROM new_merchants nm
                LEFT JOIN ods_transactions_all t
                  ON CAST(t.merchant_id AS VARCHAR) = nm.mid
                 AND t.created_at >= nm.bind_date
                 AND t.created_at < nm.bind_date + INTERVAL 30 DAY
                 AND t.created_at < report_cutoff()
                 AND {successful_transaction_where('t')}
                GROUP BY nm.mid, nm.sales_name
            )
            SELECT
                COUNT(*) AS new_merchants,
                COALESCE(SUM(volume), 0) AS new_volume,
                CASE WHEN COUNT(*) > 0
                     THEN COALESCE(SUM(volume), 0) / COUNT(*) ELSE 0 END AS avg_volume,
                COUNT(DISTINCT CASE WHEN volume > 0 THEN sales_name END) AS active_sales
            FROM first_30d
        """
    else:
        sql = f"""
            WITH active_merchants AS (
                SELECT
                    CAST(t.merchant_id AS VARCHAR) AS mid,
                    mt.业务员 AS sales_name,
                    SUM(t.amount) AS volume
                FROM ods_transactions_all t
                JOIN merchants m
                  ON CAST(m.id AS VARCHAR) = CAST(t.merchant_id AS VARCHAR)
                JOIN merchant_trans_30 mt
                  ON CAST(mt.商户编号 AS VARCHAR) = CAST(m.id AS VARCHAR)
                WHERE t.created_at >= DATE_TRUNC('MONTH', report_date())
                  AND t.created_at < report_cutoff()
                  AND {successful_transaction_where('t')}
                  AND m.status != '注销' AND {HW}
                  AND {w}
                GROUP BY CAST(t.merchant_id AS VARCHAR), mt.业务员
            )
            SELECT
                COUNT(DISTINCT mid) AS active_merchants,
                COALESCE(SUM(volume), 0) AS existing_volume,
                CASE WHEN COUNT(DISTINCT mid) > 0
                     THEN COALESCE(SUM(volume), 0) / COUNT(DISTINCT mid) ELSE 0 END AS avg_volume,
                COUNT(DISTINCT sales_name) AS active_sales
            FROM active_merchants
        """
    return duckdb_client.query_one(sql, p) or {}


@router.get("/rank")
def rank(
    period: Literal["monthly", "weekly"] = Query("monthly", description="monthly / weekly"),
    agent_id: str = Query(None),
    sales_person: str = Query(None),
    current_user=Depends(require_roles("admin", "supervisor")),
):
    """增量：本期新增商户排名 Top20"""
    agent_ids, sales_names = resolve_scope_values(current_user, agent_id, sales_person)
    trunc = "MONTH" if period == "monthly" else "WEEK"
    w, p = _scope(agent_ids, sales_names, current_user["role"] != "admin")
    sql = f"""
        SELECT
            mt.业务员 AS sales_name,
            COUNT(DISTINCT m.id) AS new_merchants,
            COALESCE(SUM(t.amount), 0) AS new_volume
        FROM merchants m
        JOIN merchant_trans_30 mt ON CAST(mt.商户编号 AS VARCHAR) = CAST(m.id AS VARCHAR)
        LEFT JOIN ods_transactions_all t ON CAST(t.merchant_id AS VARCHAR) = CAST(m.id AS VARCHAR)
            AND t.created_at >= CAST(m.first_bound_time AS DATE)
            AND t.created_at < CAST(m.first_bound_time AS DATE) + INTERVAL 30 DAY
            AND t.created_at < report_cutoff()
            AND {successful_transaction_where('t')}
        WHERE m.first_bound_time >= DATE_TRUNC('{trunc}', report_date())
          AND m.first_bound_time < report_cutoff()
          AND m.status != '注销' AND {HW}
          AND {w}
        GROUP BY 1 ORDER BY new_merchants DESC, sales_name ASC LIMIT 20
    """
    return duckdb_client.query_all(sql, p)


@router.get("/history")
def history(
    period: Literal["monthly", "weekly"] = Query("monthly", description="monthly / weekly"),
    data_type: Literal["new", "existing"] = Query("new", description="new(增量) / existing(存量)"),
    months: int = Query(6, ge=1, le=24, description="回溯月数"),
    agent_id: str = Query(None),
    sales_person: str = Query(None),
    current_user=Depends(require_roles("admin", "supervisor")),
):
    """热力表格：按销售 × 时间 的增量或存量历史数据

    增量：merchants.first_bound_time → 每月新增商户 + 首月交易
    存量：transaction_amounts → 每月活跃商户 + 交易额
    """
    agent_ids, sales_names = resolve_scope_values(current_user, agent_id, sales_person)
    trunc = "MONTH" if period == "monthly" else "WEEK"
    w, p = _scope(agent_ids, sales_names, current_user["role"] != "admin")
    window_months = months - 1 if period == "monthly" else months

    if data_type == "new":
        # 增量：新绑定商户 + 首月交易
        sql = f"""
            SELECT
                mt.业务员 AS sales_name,
                DATE_TRUNC('{trunc}', CAST(m.first_bound_time AS DATE)) AS period,
                COUNT(DISTINCT m.id) AS merchants,
                COUNT(t.order_no) AS txn_count,
                COALESCE(SUM(t.amount), 0) AS total_volume,
                CASE WHEN COUNT(DISTINCT m.id) > 0 THEN COALESCE(SUM(t.amount),0)/COUNT(DISTINCT m.id) ELSE 0 END AS avg_volume
            FROM merchants m
            JOIN merchant_trans_30 mt ON CAST(mt.商户编号 AS VARCHAR) = CAST(m.id AS VARCHAR)
            LEFT JOIN ods_transactions_all t ON CAST(t.merchant_id AS VARCHAR) = CAST(m.id AS VARCHAR)
                AND t.created_at >= CAST(m.first_bound_time AS DATE)
                AND t.created_at < CAST(m.first_bound_time AS DATE) + INTERVAL 30 DAY
                AND t.created_at < report_cutoff()
                AND {successful_transaction_where('t')}
            WHERE m.first_bound_time IS NOT NULL
          AND m.first_bound_time >= DATE_TRUNC('{trunc}', report_date()) - INTERVAL {window_months} MONTH
              AND m.first_bound_time < report_cutoff()
              AND m.status != '注销' AND {HW}
              AND {w}
            GROUP BY 1, 2 ORDER BY period ASC, merchants DESC, sales_name ASC
        """
    else:
        # 存量：每月成功交易活跃商户 + 成功交易额。
        sql = f"""
            WITH monthly_data AS (
                SELECT
                    t.merchant_id,
                    DATE_TRUNC('{trunc}', t.created_at) AS period,
                    SUM(t.amount) AS amount,
                    COUNT(*) AS txn_count
                FROM ods_transactions_all t
                WHERE t.created_at >= DATE_TRUNC('{trunc}', report_date()) - INTERVAL {window_months} MONTH
                  AND t.created_at < report_cutoff()
                  AND {successful_transaction_where('t')}
                GROUP BY 1, 2
            )
            SELECT
                mt.业务员 AS sales_name,
                md.period,
                COUNT(DISTINCT md.merchant_id) AS merchants,
                COALESCE(SUM(md.txn_count), 0) AS txn_count,
                COALESCE(SUM(md.amount), 0) AS total_volume,
                CASE WHEN COUNT(DISTINCT md.merchant_id) > 0
                     THEN COALESCE(SUM(md.amount),0)/COUNT(DISTINCT md.merchant_id)
                     ELSE 0 END AS avg_volume
            FROM monthly_data md
            JOIN merchant_trans_30 mt ON CAST(mt.商户编号 AS VARCHAR) = CAST(md.merchant_id AS VARCHAR)
            JOIN merchants m ON CAST(m.id AS VARCHAR) = CAST(md.merchant_id AS VARCHAR)
            WHERE mt.业务员 IS NOT NULL AND mt.业务员 != ''
              AND m.status != '注销' AND {HW}
              AND {w}
            GROUP BY 1, 2 ORDER BY period ASC, merchants DESC, sales_name ASC
        """
    return duckdb_client.query_all(sql, p)
