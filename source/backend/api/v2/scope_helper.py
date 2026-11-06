"""
v2 公共依赖：视角切换过滤 + 权限隔离

权限规则：
- admin：使用前端传来的 agent_id/sales_person 参数（可看全部）
- supervisor：强制只看自己名下商户（sales_person = 自己的 display_name）
- sales：强制只看自己名下商户（sales_person = 自己的 display_name）

无论前端传什么参数，supervisor/sales 都被强制限制。
"""
from fastapi import Query, Depends
from core import duckdb_client
from core.auth import get_current_user, resolve_scope_values
from core.sql_constants import merchant_binding_where


def resolve_eligible_merchant_filter(
    current_user: dict,
    agent_id=None,
    sales_person=None,
    alias: str = "m",
):
    """构建统一的有效商户范围。

    所有金额、活跃商户和户均指标都应复用此条件，保证分子和分母来自
    同一批已绑定硬件、未注销且属于当前账号数据范围的商户。
    """
    conditions = [merchant_binding_where(alias)]
    params = []
    agent_ids, sales_names = resolve_scope_values(current_user, agent_id, sales_person)
    parts = []
    if agent_ids:
        placeholders = ",".join("?" for _ in agent_ids)
        parts.append(f"CAST({alias}.agent_id AS VARCHAR) IN ({placeholders})")
        params.extend(agent_ids)
    if sales_names:
        placeholders = ",".join("?" for _ in sales_names)
        parts.append(
            f"CAST({alias}.id AS VARCHAR) IN ("
            "SELECT DISTINCT CAST(商户编号 AS VARCHAR) FROM merchant_trans_30 "
            f"WHERE 业务员 IN ({placeholders}))"
        )
        params.extend(sales_names)
    if parts:
        conditions.append("(" + " OR ".join(parts) + ")")
    elif current_user.get("role") != "admin":
        conditions.append("1=0")
    return " AND ".join(conditions), params


def build_scope_filter(
    agent_id: str = Query(None, description="代理商ID"),
    sales_person: str = Query(None, description="销售名"),
    current_user: dict = Depends(get_current_user),
):
    """
    构建视角过滤条件，返回 (where_clause, params)

    权限隔离：
    - admin 可选传 agent_id / sales_person
    - supervisor / sales 强制 sales_person = 当前用户 display_name
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
