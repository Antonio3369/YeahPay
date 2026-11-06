"""
飞虎队模块 - 截图 E
销售团队业绩榜 + 名下商户状态分布
数据来源: dim_sales_person + dim_merchant_list + ods_transactions_all
"""
from fastapi import APIRouter, Depends
from core import duckdb_client
from core.auth import require_roles

router = APIRouter(prefix="/team", tags=["飞虎队"])


@router.get("/rank")
def team_rank(_=Depends(require_roles("admin"))):
    """E - 飞虎队业绩榜"""
    # 用 dim_sales_person 关联 dim_merchant_list 拿到销售名下商户
    # 再 join 交易表算本月交易额
    sql = """
        WITH sales_merchants AS (
            SELECT
                s.sales_name,
                COUNT(DISTINCT m.merchant_id) AS merchant_count
            FROM dim_sales_person s
            LEFT JOIN dim_merchant_list m
                ON m.sales_person = s.sales_name
            GROUP BY s.sales_name
        ),
        merchant_amount AS (
            SELECT
                m.sales_person AS sales_name,
                COALESCE(SUM(t.amount), 0) AS mtd_amount,
                COUNT(t.order_no)           AS mtd_count
            FROM dim_merchant_list m
            LEFT JOIN ods_transactions_all t
                ON CAST(t.merchant_id AS VARCHAR) = CAST(m.merchant_id AS VARCHAR)
                AND t.created_at >= DATE_TRUNC('month', CURRENT_DATE)
            WHERE m.sales_person IS NOT NULL
            GROUP BY m.sales_person
        )
        SELECT
            sm.sales_name,
            sm.merchant_count,
            COALESCE(ma.mtd_amount, 0) AS mtd_amount,
            COALESCE(ma.mtd_count, 0)  AS mtd_count
        FROM sales_merchants sm
        LEFT JOIN merchant_amount ma ON ma.sales_name = sm.sales_name
        ORDER BY ma.mtd_amount DESC, sm.sales_name ASC
    """
    return duckdb_client.query_all(sql)
