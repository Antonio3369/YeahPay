"""
预警模块 - 截图 I
- 列表 + 跟进
"""
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from typing import Literal
from typing import Optional
from datetime import date

from core import sqlite_client, duckdb_client
from core.auth import get_current_user
from core.sql_constants import MERCHANT_BINDING_WHERE
from api.scope import ensure_merchant_access, scope_merchant_ids as get_scope_merchant_ids
from core.pushes import merge_scope_with_pushes

ALERT_RULE_LABELS = {
    "inactive_days": "未激活",
    "dormant_days": "已沉默",
    "weekly_decline_pct": "交易周环比下降",
    "monthly_decline_pct": "交易月环比下降",
    "failure_ratio_pct": "交易失败",
    "weekly_spike_pct": "交易异动",
}


def _group_alert_rows(rows):
    """把同一商户命中的多条规则合并为一张预警卡片。"""
    grouped = {}
    for row in rows:
        merchant_id = str(row.get("merchant_id"))
        item = grouped.setdefault(merchant_id, {**row, "tags": [], "tag_labels": []})
        key = row.get("alert_type")
        item["tags"].append({
            "key": key,
            "label": ALERT_RULE_LABELS.get(key, key),
            "metric_value": row.get("metric_value"),
            "threshold_value": row.get("threshold_value"),
            "snapshot_date": row.get("snapshot_date"),
        })
        if key not in item["tag_labels"]:
            item["tag_labels"].append(ALERT_RULE_LABELS.get(key, key))
    return list(grouped.values())


def ensure_alert_access(alert_id: int, current_user: dict):
    alert = sqlite_client.query_one("SELECT merchant_id, assigned_to FROM alerts WHERE id = ?", [alert_id])
    if not alert:
        raise HTTPException(status_code=404, detail="预警不存在")
    ensure_merchant_access(str(alert["merchant_id"]), current_user)
    if current_user["role"] == "sales" and alert.get("assigned_to") not in (None, current_user["id"]):
        raise HTTPException(status_code=403, detail="无权操作该预警")
    return alert


def update_merchant_alert_status(merchant_id: str, status: str):
    """多标签预警共用一个跟进状态，避免只处理其中一个标签。"""
    sqlite_client.execute(
        """UPDATE alerts SET status=?, updated_at=CURRENT_TIMESTAMP
           WHERE merchant_id=? AND status IN ('unfollowed','following')""",
        [status, merchant_id],
    )

router = APIRouter(prefix="/alerts", tags=["预警"])


class FollowupCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    followup_type: Literal["note", "call", "visit", "email"] = "note"


class AlertStatusUpdate(BaseModel):
    status: Literal["unfollowed", "following", "resolved", "ignored"]


class AlertResolve(BaseModel):
    touch_type: Literal["online", "offline"]
    resolved: bool
    note: str = Field(default="", max_length=2000)


@router.get("")
def list_alerts(
    status: Optional[str] = Query(None),
    alert_type: Optional[str] = Query(None),
    assigned_to: Optional[int] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    keyword: Optional[str] = Query(None, description="商户名称或ID，全量搜索"),
    tags: Optional[str] = Query(None, description="逗号分隔的规则标签 key"),
    agent_id: Optional[str] = Query(None),
    sales_person: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user=Depends(get_current_user),
):
    """I - 预警列表（支持 agent_id / sales_person 视角过滤）"""
    # 视角过滤：从 DuckDB 查出对应 merchant_ids
    scope_merchant_ids = get_scope_merchant_ids(current_user, agent_id, sales_person)

    # 推送接收人即使原本不在该商户的数据范围内，也可以看到该商户的系统预警。
    scope_merchant_ids = merge_scope_with_pushes(scope_merchant_ids, current_user)

    conditions = ["1=1"]
    params = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if alert_type:
        conditions.append("alert_type = ?")
        params.append(alert_type)
    if assigned_to:
        conditions.append("assigned_to = ?")
        params.append(assigned_to)
    if start_date:
        conditions.append("snapshot_date >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("snapshot_date <= ?")
        params.append(end_date)

    # 视角过滤
    if scope_merchant_ids is not None:
        if len(scope_merchant_ids) == 0:
            return {"total": 0, "page": page, "page_size": page_size, "has_more": False, "items": []}
        placeholders = ",".join(["?"] * len(scope_merchant_ids))
        conditions.append(f"merchant_id IN ({placeholders})")
        params.extend(scope_merchant_ids)

    # 销售只能看分配给自己的预警
    if current_user["role"] == "sales":
        conditions.append("(assigned_to = ? OR assigned_to IS NULL)")
        params.append(current_user["id"])

    if keyword:
        conditions.append("(merchant_id LIKE ? OR merchant_name LIKE ? OR title LIKE ? OR description LIKE ?)")
        keyword_param = f"%{keyword.strip()}%"
        params.extend([keyword_param] * 4)
    selected_tags = [tag.strip() for tag in (tags or "").split(",") if tag.strip()]
    if selected_tags:
        placeholders = ",".join("?" for _ in selected_tags)
        conditions.append(f"alert_type IN ({placeholders})")
        params.extend(selected_tags)

    where = " AND ".join(conditions)
    raw_rows = sqlite_client.query_all(
        f"SELECT * FROM alerts WHERE {where} ORDER BY created_at DESC, id DESC", params
    )
    grouped = _group_alert_rows(raw_rows)
    if selected_tags:
        # 多选采用交集：同一商户必须同时命中所有已选规则。
        selected_tag_set = set(selected_tags)
        grouped = [
            item for item in grouped
            if selected_tag_set.issubset({tag.get("key") for tag in item.get("tags", [])})
        ]
    grouped.sort(key=lambda item: (item.get("created_at") or "", item.get("id") or 0), reverse=True)
    total = len(grouped)
    offset = (page - 1) * page_size
    rows = grouped[offset:offset + page_size]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < total,
        "items": rows,
    }


@router.get("/{alert_id}")
def get_alert(alert_id: int, current_user=Depends(get_current_user)):
    """预警详情 + 跟进记录"""
    alert_access = ensure_alert_access(alert_id, current_user)
    alert = sqlite_client.query_one("SELECT * FROM alerts WHERE id = ?", [alert_id])
    if not alert:
        raise HTTPException(status_code=404, detail="预警不存在")
    followups = sqlite_client.query_all(
        """
        SELECT f.*, u.display_name AS user_name
        FROM alert_followups f
        LEFT JOIN users u ON u.id = f.user_id
        WHERE f.alert_id = ?
        ORDER BY f.created_at ASC
        """,
        [alert_id],
    )
    return {**alert, "followups": followups}


@router.post("/{alert_id}/followup")
def add_followup(
    alert_id: int,
    req: FollowupCreate,
    current_user=Depends(get_current_user),
):
    """添加跟进记录"""
    alert_access = ensure_alert_access(alert_id, current_user)
    alert = sqlite_client.query_one(
        "SELECT merchant_id, merchant_name FROM alerts WHERE id = ?", [alert_id]
    )

    followup_id = sqlite_client.execute(
        """
        INSERT INTO alert_followups (alert_id, user_id, content, followup_type)
        VALUES (?, ?, ?, ?)
        """,
        [alert_id, current_user["id"], req.content, req.followup_type],
    )
    sqlite_client.execute(
        """
        INSERT INTO followup_records
            (source_type, source_id, merchant_id, merchant_name, user_id, touch_type, result, note)
        VALUES ('alert', ?, ?, ?, ?, ?, 'following', ?)
        """,
        [
            alert_id,
            alert["merchant_id"],
            alert.get("merchant_name") or "",
            current_user["id"],
            "offline" if req.followup_type == "visit" else ("online" if req.followup_type in ("call", "email") else None),
            req.content,
        ],
    )

    # 自动更新预警状态为"跟进中"
    update_merchant_alert_status(alert_access["merchant_id"], "following")

    return {"id": followup_id, "message": "跟进成功"}


@router.put("/{alert_id}/status")
def update_alert_status(
    alert_id: int,
    req: Optional[AlertStatusUpdate] = None,
    status: Optional[str] = Query(None),
    current_user=Depends(get_current_user),
):
    """更新预警状态（resolved/ignored）"""
    status_value = req.status if req else status
    if status_value not in ("unfollowed", "following", "resolved", "ignored"):
        raise HTTPException(status_code=400, detail="无效的状态")

    alert_access = ensure_alert_access(alert_id, current_user)

    if status_value in ("following", "resolved", "ignored"):
        update_merchant_alert_status(alert_access["merchant_id"], status_value)
    else:
        sqlite_client.execute(
            "UPDATE alerts SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            [status_value, alert_id],
        )
    return {"message": "状态已更新"}


@router.post("/{alert_id}/resolve")
def resolve_alert(
    alert_id: int,
    req: AlertResolve,
    current_user=Depends(get_current_user),
):
    """记录触达方式和备注；确认未解决时保留在跟进中。"""
    alert_access = ensure_alert_access(alert_id, current_user)
    alert = sqlite_client.query_one(
        "SELECT merchant_id, merchant_name FROM alerts WHERE id = ?", [alert_id]
    )
    content = req.note.strip() or (
        ("线上触达" if req.touch_type == "online" else "线下触达")
        + ("，已解决" if req.resolved else "，暂未解决")
    )
    sqlite_client.execute(
        """
        INSERT INTO alert_followups (alert_id, user_id, content, followup_type)
        VALUES (?, ?, ?, ?)
        """,
        [alert_id, current_user["id"], content, "call" if req.touch_type == "online" else "visit"],
    )
    sqlite_client.execute(
        """
        INSERT INTO followup_records
            (source_type, source_id, merchant_id, merchant_name, user_id, touch_type, result, note)
        VALUES ('alert', ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            alert_id,
            alert["merchant_id"],
            alert.get("merchant_name") or "",
            current_user["id"],
            req.touch_type,
            "resolved" if req.resolved else "following",
            req.note.strip(),
        ],
    )
    status_value = "resolved" if req.resolved else "following"
    update_merchant_alert_status(alert_access["merchant_id"], status_value)
    return {"message": "已标记解决" if req.resolved else "已保存跟进记录，仍在跟进中", "status": status_value}
