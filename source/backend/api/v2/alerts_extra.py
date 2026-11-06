"""
v2 预警接口 - 汇总统计
支持 agent_id / sales_person 视角过滤
"""
from fastapi import APIRouter, Depends, Query
from core import sqlite_client, duckdb_client
from core.auth import get_current_user
from core.sql_constants import MERCHANT_BINDING_WHERE
from core.pushes import visible_push_counts, merge_scope_with_pushes
from api.scope import scope_merchant_ids as get_scope_merchant_ids
from core.alert_rules import ALERT_RULES

router = APIRouter(prefix="/v2/alerts", tags=["v2-预警"])


@router.get("/rules")
def alert_rules(_=Depends(get_current_user)):
    """预警页标签选项，名称与阈值页保持同一来源。"""
    return [{"key": r["key"], "label": r["display_name"]} for r in ALERT_RULES]


@router.get("/summary")
def alerts_summary(
    agent_id: str = Query(None, description="代理商ID"),
    sales_person: str = Query(None, description="销售名"),
    current_user=Depends(get_current_user),
):
    """预警统计卡（4个数字）— 按视角过滤"""
    scope_merchant_ids = get_scope_merchant_ids(current_user, agent_id, sales_person)
    scope_merchant_ids = merge_scope_with_pushes(scope_merchant_ids, current_user)

    conditions = []
    params = []
    # 根据 scope 过滤 SQL
    if scope_merchant_ids is not None:
        if len(scope_merchant_ids) == 0:
            return {
                "unfollowed": 0,
                "supervisor_review": 0,
                "pending_reply": 0,
                "overdue": 0,
                "total": 0,
            }
        placeholders = ",".join(["?"] * len(scope_merchant_ids))
        conditions.append(f"merchant_id IN ({placeholders})")
        params.extend(scope_merchant_ids)
    # 与列表接口保持一致：销售只能统计分配给自己或尚未分配的系统预警。
    if current_user["role"] == "sales":
        conditions.append("(assigned_to = ? OR assigned_to IS NULL)")
        params.append(current_user["id"])
    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

    rows = sqlite_client.query_all(
        f"SELECT status, COUNT(DISTINCT merchant_id) as cnt FROM alerts {where_clause} GROUP BY status",
        params,
    )
    stats = {"unfollowed": 0, "following": 0, "resolved": 0, "ignored": 0}
    for r in rows:
        stats[r["status"]] = r["cnt"]

    # 人工推送在未解决前也计入“跟进中”，解决后计入“已解决”。
    push_stats = visible_push_counts(current_user["id"])
    stats["following"] += push_stats.get("following", 0)
    stats["resolved"] += push_stats.get("resolved", 0)

    return {
        "unfollowed": stats["unfollowed"],
        "following": stats["following"],
        "resolved": stats["resolved"],
        "ignored": stats["ignored"],
        "supervisor_review": 0,
        "pending_reply": 0,
        "overdue": 0,
        # “全部”只统计当前预警工作流中的三种状态；历史 ignored 是重算留痕，
        # 不应该把统计卡片放大，也不应出现在前端的工作数量里。
        "total": stats["unfollowed"] + stats["following"] + stats["resolved"],
    }
