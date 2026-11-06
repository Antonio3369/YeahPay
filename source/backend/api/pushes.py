"""商户人工推送、接收、跟进和解决接口。"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core import duckdb_client, sqlite_client
from core.auth import get_current_user
from core.sql_constants import MERCHANT_BINDING_WHERE_M
from api.scope import ensure_merchant_access

router = APIRouter(prefix="/pushes", tags=["商户推送"])

# 人工推送本身不重复保存预警标签，列表筛选时关联 alerts 表取得当前商户命中的规则。
# 这里保持与 api/alerts.py、前端标签 key 一致，避免推送列表出现另一套标签名称。
ALERT_RULE_LABELS = {
    "inactive_days": "未激活",
    "dormant_days": "已沉默",
    "weekly_decline_pct": "交易周环比下降",
    "monthly_decline_pct": "交易月环比下降",
    "failure_ratio_pct": "交易失败",
    "weekly_spike_pct": "交易异动",
}


class PushCreate(BaseModel):
    merchant_id: str = Field(min_length=1, max_length=100)
    recipient_id: int = Field(gt=0)
    note: str = Field(default="", max_length=1000)


class PushResolve(BaseModel):
    touch_type: Literal["online", "offline"]
    resolved: bool
    note: str = Field(default="", max_length=2000)


def _push_or_404(push_id: int):
    push = sqlite_client.query_one(
        """
        SELECT p.*, sender.display_name AS sender_name, receiver.display_name AS receiver_name
        FROM merchant_pushes p
        LEFT JOIN users sender ON sender.id = p.pushed_by
        LEFT JOIN users receiver ON receiver.id = p.pushed_to
        WHERE p.id = ?
        """,
        [push_id],
    )
    if not push:
        raise HTTPException(status_code=404, detail="推送记录不存在")
    return push


def _check_push_visibility(push, current_user):
    if current_user["id"] not in (push["pushed_by"], push["pushed_to"]):
        raise HTTPException(status_code=403, detail="无权查看该推送")


def _format_push(push, current_user):
    item = dict(push)
    item["is_sender"] = current_user["id"] == push["pushed_by"]
    item["is_recipient"] = current_user["id"] == push["pushed_to"]
    item["needs_acceptance"] = item["is_recipient"] and not push["accepted_at"] and push["status"] == "following"
    item["source_label"] = "我推送的" if item["is_sender"] else "推送给我的"
    tag_keys = [key for key in str(item.pop("alert_tag_keys", "") or "").split(",") if key]
    item["alert_tags"] = [{"key": key, "label": ALERT_RULE_LABELS.get(key, key)} for key in tag_keys]
    item["alert_tag_labels"] = [tag["label"] for tag in item["alert_tags"]]
    return item


@router.get("/recipients")
def list_recipients(current_user=Depends(get_current_user)):
    """商户详情页的推送目标列表。"""
    rows = sqlite_client.query_all(
        """
        SELECT id, username, display_name, role
        FROM users
        WHERE is_active = 1 AND id != ?
        ORDER BY CASE role WHEN 'admin' THEN 0 WHEN 'supervisor' THEN 1 ELSE 2 END, display_name
        """,
        [current_user["id"]],
    )
    role_labels = {"admin": "管理员", "supervisor": "代理商", "sales": "销售"}
    for row in rows:
        row["role_label"] = role_labels.get(row["role"], row["role"])
        row["label"] = f"{row['display_name']} · {row['role_label']}"
    return rows


@router.post("")
def create_push(req: PushCreate, current_user=Depends(get_current_user)):
    """从商户详情页把商户推送给指定账号。"""
    ensure_merchant_access(req.merchant_id, current_user)

    recipient = sqlite_client.query_one(
        "SELECT id, display_name FROM users WHERE id = ? AND is_active = 1",
        [req.recipient_id],
    )
    if not recipient:
        raise HTTPException(status_code=404, detail="目标账号不存在或已停用")
    if req.recipient_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="不能推送给自己")

    merchant = duckdb_client.query_one(
        f"""
        SELECT id, COALESCE(short_name, name, CAST(id AS VARCHAR)) AS merchant_name
        FROM merchants m WHERE CAST(m.id AS VARCHAR) = ? AND {MERCHANT_BINDING_WHERE_M}
        """,
        [req.merchant_id],
    )
    if not merchant:
        raise HTTPException(status_code=404, detail="商户不存在")

    existing = sqlite_client.query_one(
        """
        SELECT id FROM merchant_pushes
        WHERE merchant_id = ? AND pushed_by = ? AND pushed_to = ? AND status = 'following'
        """,
        [str(req.merchant_id), current_user["id"], req.recipient_id],
    )
    if existing:
        raise HTTPException(status_code=409, detail="该商户已经推送给此账号且仍在跟进中")

    push_id = sqlite_client.execute(
        """
        INSERT INTO merchant_pushes (merchant_id, merchant_name, push_note, pushed_by, pushed_to, status)
        VALUES (?, ?, ?, ?, ?, 'following')
        """,
        [str(merchant["id"]), merchant["merchant_name"], req.note.strip(), current_user["id"], req.recipient_id],
    )
    return {"id": push_id, "message": "商户已推送", "recipient_name": recipient["display_name"]}


@router.get("/inbox")
def list_inbox(current_user=Depends(get_current_user)):
    """接收人尚未点击开始跟进的推送，显示在预警页最前面。"""
    rows = sqlite_client.query_all(
        """
        SELECT p.*, sender.display_name AS sender_name, receiver.display_name AS receiver_name
        FROM merchant_pushes p
        LEFT JOIN users sender ON sender.id = p.pushed_by
        LEFT JOIN users receiver ON receiver.id = p.pushed_to
        WHERE p.pushed_to = ? AND p.status = 'following' AND p.accepted_at IS NULL
        ORDER BY p.created_at DESC, p.id DESC
        """,
        [current_user["id"]],
    )
    return {
        "total": len(rows),
        "merchant_total": len({str(row["merchant_id"]) for row in rows}),
        "items": [_format_push(row, current_user) for row in rows],
    }


@router.get("")
def list_pushes(
    status: Literal["following", "resolved"] = Query("following"),
    keyword: Optional[str] = Query(None, description="商户名称或ID，全量搜索"),
    tags: Optional[str] = Query(None, description="逗号分隔的预警规则 key，多选为交集"),
    current_user=Depends(get_current_user),
):
    """推送双方都可见；状态、标签和搜索条件与预警列表保持一致。"""
    selected_tags = [tag.strip() for tag in (tags or "").split(",") if tag.strip()]
    conditions = [
        "(p.pushed_by = ? OR p.pushed_to = ?)",
        "p.status = ?",
    ]
    params = [current_user["id"], current_user["id"], status]

    if keyword and keyword.strip():
        keyword_param = f"%{keyword.strip()}%"
        conditions.append("(p.merchant_id LIKE ? OR p.merchant_name LIKE ?)")
        params.extend([keyword_param, keyword_param])

    if selected_tags:
        placeholders = ",".join("?" for _ in selected_tags)
        conditions.append(
            f"p.merchant_id IN ("
            f"SELECT a_filter.merchant_id FROM alerts a_filter "
            f"WHERE a_filter.alert_type IN ({placeholders}) "
            f"AND a_filter.status IN ('unfollowed', 'following', 'resolved') "
            f"GROUP BY a_filter.merchant_id "
            f"HAVING COUNT(DISTINCT a_filter.alert_type) = ?"
            f")"
        )
        params.extend(selected_tags)
        params.append(len(set(selected_tags)))

    where = " AND ".join(conditions)
    rows = sqlite_client.query_all(
        f"""
        SELECT p.*, sender.display_name AS sender_name, receiver.display_name AS receiver_name,
               COALESCE((
                   SELECT GROUP_CONCAT(DISTINCT a_tag.alert_type)
                   FROM alerts a_tag
                   WHERE a_tag.merchant_id = p.merchant_id
                     AND a_tag.status IN ('unfollowed', 'following', 'resolved')
               ), '') AS alert_tag_keys
        FROM merchant_pushes p
        LEFT JOIN users sender ON sender.id = p.pushed_by
        LEFT JOIN users receiver ON receiver.id = p.pushed_to
        WHERE {where}
        ORDER BY p.created_at DESC, p.id DESC
        """,
        params,
    )
    return {
        "total": len(rows),
        "merchant_total": len({str(row["merchant_id"]) for row in rows}),
        "items": [_format_push(row, current_user) for row in rows],
    }


@router.post("/{push_id}/start")
def start_push(push_id: int, current_user=Depends(get_current_user)):
    push = _push_or_404(push_id)
    if current_user["id"] != push["pushed_to"]:
        raise HTTPException(status_code=403, detail="只有被推送账号可以开始跟进")
    if push["status"] != "following":
        raise HTTPException(status_code=400, detail="该推送已经结束")
    sqlite_client.execute(
        "UPDATE merchant_pushes SET accepted_at = COALESCE(accepted_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        [push_id],
    )
    sqlite_client.execute(
        """
        INSERT INTO followup_records
            (source_type, source_id, merchant_id, merchant_name, user_id, touch_type, result, note)
        VALUES ('push', ?, ?, ?, ?, NULL, 'following', '开始跟进')
        """,
        [push_id, push["merchant_id"], push.get("merchant_name") or "", current_user["id"]],
    )
    return {"message": "已开始跟进"}


@router.post("/{push_id}/resolve")
def resolve_push(push_id: int, req: PushResolve, current_user=Depends(get_current_user)):
    push = _push_or_404(push_id)
    if current_user["id"] != push["pushed_to"]:
        raise HTTPException(status_code=403, detail="只有被推送账号可以提交解决结果")
    if push["status"] != "following":
        raise HTTPException(status_code=400, detail="该推送已经结束")
    if not push["accepted_at"]:
        raise HTTPException(status_code=400, detail="请先点击开始跟进")

    status = "resolved" if req.resolved else "following"
    sqlite_client.execute(
        """
        UPDATE merchant_pushes
        SET status = ?, touch_type = ?, resolved = ?, resolution_note = ?,
            resolved_by = CASE WHEN ? THEN ? ELSE resolved_by END,
            resolved_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE resolved_at END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        [status, req.touch_type, int(req.resolved), req.note, int(req.resolved), current_user["id"], int(req.resolved), push_id],
    )
    sqlite_client.execute(
        """
        INSERT INTO followup_records
            (source_type, source_id, merchant_id, merchant_name, user_id, touch_type, result, note)
        VALUES ('push', ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            push_id,
            push["merchant_id"],
            push.get("merchant_name") or "",
            current_user["id"],
            req.touch_type,
            status,
            req.note.strip(),
        ],
    )
    return {"message": "已标记解决" if req.resolved else "已保存跟进记录，仍在跟进中", "status": status}


@router.get("/{push_id}")
def get_push(push_id: int, current_user=Depends(get_current_user)):
    push = _push_or_404(push_id)
    _check_push_visibility(push, current_user)
    return _format_push(push, current_user)
