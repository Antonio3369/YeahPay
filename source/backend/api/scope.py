"""
视角切换模块
管理员可以切换查看：全机构 / 某代理商 / 某销售 的数据
"""
from fastapi import APIRouter, Depends, Query
from core import duckdb_client
from core.auth import get_current_user, require_roles, resolve_scope_values
from core.sql_constants import MERCHANT_BINDING_WHERE, merchant_binding_where

router = APIRouter(prefix="/scope", tags=["视角切换"])


def scope_merchant_ids(current_user: dict, agent_id=None, sales_person=None):
    """返回当前账号/视角对应的商户 ID 并集；管理员未选视角时返回 None。"""
    agent_ids, sales_names = resolve_scope_values(current_user, agent_id, sales_person)
    if not agent_ids and not sales_names:
        return None if current_user.get("role") == "admin" else []
    parts = []
    params = []
    if agent_ids:
        placeholders = ",".join("?" for _ in agent_ids)
        parts.append(
            f"SELECT CAST(id AS VARCHAR) AS mid FROM merchants WHERE CAST(agent_id AS VARCHAR) IN ({placeholders}) AND {MERCHANT_BINDING_WHERE}"
        )
        params.extend(agent_ids)
    if sales_names:
        placeholders = ",".join("?" for _ in sales_names)
        parts.append(
            "SELECT DISTINCT CAST(mt.商户编号 AS VARCHAR) AS mid "
            "FROM merchant_trans_30 mt "
            "JOIN merchants m ON CAST(m.id AS VARCHAR) = CAST(mt.商户编号 AS VARCHAR) "
            f"WHERE mt.业务员 IN ({placeholders}) "
            f"AND {merchant_binding_where('m')}"
        )
        params.extend(sales_names)
    rows = duckdb_client.query_all(" UNION ".join(parts), params)
    return [r["mid"] for r in rows]


@router.get("/agents")
def list_agents(_=Depends(require_roles("admin"))):
    """获取代理商列表（用于视角切换下拉）"""
    sql = f"""
        SELECT 
            CAST(agent_id AS VARCHAR) AS agent_id,
            agent_name,
            COUNT(DISTINCT id) AS merchant_count
        FROM merchants
        WHERE agent_id IS NOT NULL AND {MERCHANT_BINDING_WHERE}
        GROUP BY agent_id, agent_name
        ORDER BY merchant_count DESC, CAST(agent_id AS VARCHAR)
    """
    return duckdb_client.query_all(sql)


@router.get("/sales")
def list_sales(_=Depends(require_roles("admin"))):
    """获取销售列表（用于视角切换下拉）"""
    sql = f"""
        SELECT
            mt.业务员 AS sales_name,
            COUNT(DISTINCT m.id) AS merchant_count
        FROM merchant_trans_30 mt
        JOIN merchants m ON CAST(m.id AS VARCHAR)=CAST(mt.商户编号 AS VARCHAR)
        WHERE mt.业务员 IS NOT NULL AND mt.业务员 != ''
          AND {merchant_binding_where('m')}
        GROUP BY mt.业务员
        ORDER BY merchant_count DESC, mt.业务员
    """
    return duckdb_client.query_all(sql)


def build_scope_filter(
    agent_id: str = Query(None, description="代理商ID"),
    sales_person: str = Query(None, description="销售名"),
    current_user: dict = Depends(get_current_user),
):
    """
    FastAPI 依赖：构建视角过滤条件
    返回 (where_clause, params) 供 DuckDB 查询拼接
    
    用法:
        @router.get("/xxx")
        def xxx(scope: tuple = Depends(build_scope_filter)):
            where, params = scope
            sql = f"SELECT ... FROM ods_transactions_all WHERE {where}"
    """
    conditions = ["1=1"]
    params = []

    agent_ids, sales_names = resolve_scope_values(current_user, agent_id, sales_person)
    if agent_ids or sales_names:
        parts = []
        if agent_ids:
            placeholders = ",".join("?" for _ in agent_ids)
            parts.append(f"CAST(agent_id AS VARCHAR) IN ({placeholders})")
            params.extend(agent_ids)
        if sales_names:
            placeholders = ",".join("?" for _ in sales_names)
            parts.append(
                "CAST(merchant_id AS VARCHAR) IN ("
                f"SELECT DISTINCT CAST(商户编号 AS VARCHAR) FROM merchant_trans_30 WHERE 业务员 IN ({placeholders})"
                ")"
            )
            params.extend(sales_names)
        conditions.append("(" + " OR ".join(parts) + ")")
    elif current_user.get("role") != "admin":
        conditions.append("1=0")

    where = " AND ".join(conditions)
    return (where, params)


def ensure_merchant_access(merchant_id: str, current_user: dict):
    """阻止非管理员通过详情接口读取其他销售名下的商户。"""
    if current_user["role"] == "admin":
        return
    agent_ids, sales_names = resolve_scope_values(current_user)
    accessible = None
    if agent_ids:
        placeholders = ",".join("?" for _ in agent_ids)
        accessible = duckdb_client.query_one(
            "SELECT 1 AS ok FROM merchants m WHERE CAST(m.id AS VARCHAR) = ? "
            f"AND CAST(m.agent_id AS VARCHAR) IN ({placeholders}) "
            f"AND {merchant_binding_where('m')} LIMIT 1",
            [str(merchant_id), *agent_ids],
        )
    if not accessible and sales_names:
        placeholders = ",".join("?" for _ in sales_names)
        accessible = duckdb_client.query_one(
            "SELECT 1 AS ok FROM merchant_trans_30 mt "
            "JOIN merchants m ON CAST(m.id AS VARCHAR)=CAST(mt.商户编号 AS VARCHAR) "
            f"WHERE CAST(mt.商户编号 AS VARCHAR) = ? AND mt.业务员 IN ({placeholders}) "
            f"AND {merchant_binding_where('m')} LIMIT 1",
            [str(merchant_id), *sales_names],
        )
    # 被推送账号在推送未解决前也必须能打开该商户详情。
    if not accessible:
        from core import sqlite_client
        accessible = sqlite_client.query_one(
            """
            SELECT 1 AS ok FROM merchant_pushes
            WHERE merchant_id = ? AND pushed_to = ? AND status = 'following'
            LIMIT 1
            """,
            [str(merchant_id), current_user["id"]],
        )
    if not accessible:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="无权访问该商户")
