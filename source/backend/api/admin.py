"""
后台管理模块 - 截图 M/N/O/P/Q
- O: 销售账号管理
- P: 阈值配置
- N: 导入批次
- Q: 其他设置
"""
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from typing import Literal, Optional, List

from core import sqlite_client, auth, duckdb_client
from core.auth import get_current_user, require_roles

router = APIRouter(prefix="/admin", tags=["后台管理"])


# ============================================================
# O - 销售账号管理
# ============================================================
class UserCreate(BaseModel):
    username: str
    password: str = Field(min_length=6, max_length=200)
    display_name: str
    role: Literal["admin", "supervisor", "sales"]
    supervisor_id: Optional[int] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    scope_type: Literal["all", "agent", "sales"] = "all"
    scope_value: Optional[str] = None
    # 新版多范围绑定：代理商账号可选一个代理商并多选销售。
    agent_id: Optional[str] = None
    sales_names: List[str] = Field(default_factory=list)


class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    role: Optional[Literal["admin", "supervisor", "sales"]] = None
    supervisor_id: Optional[int] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    is_active: Optional[bool] = None
    scope_type: Optional[Literal["all", "agent", "sales"]] = None
    scope_value: Optional[str] = None
    agent_id: Optional[str] = None
    sales_names: Optional[List[str]] = None


def _validate_scope(role: str, agent_id: Optional[str], sales_names: List[str]):
    """校验角色与固定数据范围。

    supervisor（代理商账号）支持：0/1 个代理商 + 0..N 个销售；
    sales（销售账号）必须且只能绑定 1 个销售。
    """
    if role == "admin":
        if agent_id or sales_names:
            raise HTTPException(status_code=400, detail="管理员不能绑定代理商或销售范围")
        return
    clean_agent = str(agent_id).strip() if agent_id else None
    clean_sales = list(dict.fromkeys(str(v).strip() for v in (sales_names or []) if str(v).strip()))
    if role == "sales" and (clean_agent or len(clean_sales) != 1):
        raise HTTPException(status_code=400, detail="销售账号必须绑定且只能绑定一个销售")
    if clean_agent:
        found = duckdb_client.query_one(
            "SELECT 1 AS ok FROM merchants WHERE CAST(agent_id AS VARCHAR) = ? LIMIT 1", [clean_agent]
        )
        if not found:
            raise HTTPException(status_code=400, detail="绑定的代理商在当前数据库中不存在")
    if clean_sales:
        placeholders = ",".join("?" for _ in clean_sales)
        found = duckdb_client.query_one(
            f"SELECT 1 AS ok FROM merchant_trans_30 WHERE 业务员 IN ({placeholders}) LIMIT 1", clean_sales
        )
        found_names = duckdb_client.query_all(
            f"SELECT DISTINCT 业务员 AS name FROM merchant_trans_30 WHERE 业务员 IN ({placeholders})", clean_sales
        )
        if not found or len(found_names) != len(clean_sales):
            raise HTTPException(status_code=400, detail="部分销售不在当前数据库中")


def _save_scope(user_id: int, bindings: list[dict]):
    """保存新版多范围绑定，同时维护旧表的单范围兼容记录。"""
    sqlite_client.execute("DELETE FROM user_scope_bindings WHERE user_id = ?", [user_id])
    sqlite_client.execute("DELETE FROM user_scopes WHERE user_id = ?", [user_id])
    if bindings:
        sqlite_client.execute_many(
            "INSERT INTO user_scope_bindings (user_id, scope_type, scope_value) VALUES (?, ?, ?)",
            [(user_id, b["scope_type"], b["scope_value"]) for b in bindings],
        )
    # 旧代码只理解一条范围，只有唯一绑定时才写入，避免多选账号被误判为全机构。
    if len(bindings) == 1:
        b = bindings[0]
        sqlite_client.execute(
            "INSERT INTO user_scopes (user_id, scope_type, scope_value) VALUES (?, ?, ?)",
            [user_id, b["scope_type"], b["scope_value"]],
        )


def _bindings_from_request(role, scope_type=None, scope_value=None, agent_id=None, sales_names=None):
    """把新版字段和旧版字段统一成绑定列表。"""
    if role == "admin":
        return []
    if agent_id is not None or sales_names is not None:
        bindings = []
        if agent_id:
            bindings.append({"scope_type": "agent", "scope_value": str(agent_id).strip()})
        bindings.extend(
            {"scope_type": "sales", "scope_value": str(v).strip()}
            for v in dict.fromkeys(sales_names or []) if str(v).strip()
        )
        return bindings
    if scope_type in ("agent", "sales") and scope_value:
        return [{"scope_type": scope_type, "scope_value": str(scope_value).strip()}]
    return []


@router.get("/users")
def list_users(_=Depends(require_roles("admin"))):
    """O - 销售账号列表"""
    users = sqlite_client.query_all(
        """
        SELECT u.id, u.username, u.display_name, u.role, u.supervisor_id,
               s.display_name AS supervisor_name, u.email, u.phone,
               u.is_active, u.last_login_at, u.created_at,
               us.scope_type, us.scope_value
        FROM users u
        LEFT JOIN users s ON s.id = u.supervisor_id
        LEFT JOIN user_scopes us ON us.user_id = u.id
        ORDER BY u.role, u.id
        """
    )
    bindings = sqlite_client.query_all(
        "SELECT user_id, scope_type, scope_value FROM user_scope_bindings ORDER BY scope_type, scope_value"
    )
    by_user = {}
    for b in bindings:
        by_user.setdefault(b["user_id"], []).append(b)
    for user in users:
        rows = by_user.get(user["id"], [])
        # 兼容尚未迁移或旧版本单范围账号。
        if not rows and user.get("scope_value"):
            rows = [{"scope_type": user["scope_type"], "scope_value": user["scope_value"]}]
        user["agent_id"] = next((b["scope_value"] for b in rows if b["scope_type"] == "agent"), None)
        user["sales_names"] = [b["scope_value"] for b in rows if b["scope_type"] == "sales"]
        user["sales_count"] = len(user["sales_names"])
        labels = []
        if user["agent_id"]:
            labels.append("代理商 " + str(user["agent_id"]))
        if user["sales_names"]:
            labels.append("销售 " + "、".join(user["sales_names"]))
        user["scope_label"] = " · ".join(labels) if labels else ("全部机构" if user["role"] == "admin" else "未配置数据范围")
    return users


@router.post("/users")
def create_user(req: UserCreate, _=Depends(require_roles("admin"))):
    """O - 新建账号"""
    existing = sqlite_client.query_one("SELECT id FROM users WHERE username = ?", [req.username])
    if existing:
        raise HTTPException(status_code=400, detail="用户名已存在")

    if "agent_id" in req.model_fields_set or "sales_names" in req.model_fields_set:
        bindings = _bindings_from_request(req.role, agent_id=req.agent_id, sales_names=req.sales_names)
    else:
        bindings = _bindings_from_request(req.role, req.scope_type, req.scope_value)
    agent_id = next((b["scope_value"] for b in bindings if b["scope_type"] == "agent"), None)
    sales_names = [b["scope_value"] for b in bindings if b["scope_type"] == "sales"]
    _validate_scope(req.role, agent_id, sales_names)
    user_id = sqlite_client.execute(
        """
        INSERT INTO users (username, password_hash, display_name, role, supervisor_id, email, phone, must_change_pwd)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """,
        [
            req.username,
            auth.hash_password(req.password),
            req.display_name,
            req.role,
            req.supervisor_id,
            req.email,
            req.phone,
        ],
    )
    _save_scope(user_id, bindings)
    return {"id": user_id, "message": "创建成功，首次登录需改密"}


@router.put("/users/{user_id}")
def update_user(user_id: int, req: UserUpdate, current_user=Depends(require_roles("admin"))):
    """O - 编辑账号"""
    existing = sqlite_client.query_one(
        "SELECT role FROM users WHERE id = ?", [user_id]
    )
    if not existing:
        raise HTTPException(status_code=404, detail="用户不存在")
    updates = req.model_dump(exclude_none=True)
    if user_id == current_user["id"] and updates.get("is_active") is False:
        raise HTTPException(status_code=400, detail="不能停用自己")
    new_role = updates.get("role", existing["role"])
    current_bindings = sqlite_client.query_all(
        "SELECT scope_type, scope_value FROM user_scope_bindings WHERE user_id = ? ORDER BY id", [user_id]
    )
    if not current_bindings:
        legacy = sqlite_client.query_one("SELECT scope_type, scope_value FROM user_scopes WHERE user_id = ?", [user_id])
        current_bindings = [legacy] if legacy and legacy.get("scope_value") else []
    has_new_scope_fields = "agent_id" in req.model_fields_set or "sales_names" in req.model_fields_set
    if has_new_scope_fields:
        agent_id = updates.pop("agent_id", None)
        sales_names = updates.pop("sales_names", None)
        bindings = _bindings_from_request(new_role, agent_id=agent_id, sales_names=sales_names)
    elif "scope_type" in req.model_fields_set or "scope_value" in req.model_fields_set or "role" in updates:
        scope_type = updates.pop("scope_type", None)
        scope_value = updates.pop("scope_value", None)
        if "role" in updates:
            bindings = [] if new_role == "admin" or new_role != existing["role"] else current_bindings
        elif scope_type is None and not current_bindings:
            bindings = []
        elif scope_type is None:
            bindings = current_bindings
        else:
            bindings = _bindings_from_request(new_role, scope_type, scope_value)
    else:
        bindings = current_bindings
    agent_id = next((b["scope_value"] for b in bindings if b["scope_type"] == "agent"), None)
    sales_names = [b["scope_value"] for b in bindings if b["scope_type"] == "sales"]
    _validate_scope(new_role, agent_id, sales_names)
    sets = []
    params = []
    for field, value in updates.items():
        sets.append(f"{field} = ?")
        params.append(value)
    if not sets:
        raise HTTPException(status_code=400, detail="无更新字段")

    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(user_id)
    sqlite_client.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params)
    _save_scope(user_id, bindings)
    return {"message": "更新成功"}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, current_user=Depends(require_roles("admin"))):
    """O - 停用账号（软删除）"""
    if user_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="不能停用自己")
    sqlite_client.execute("UPDATE users SET is_active = 0 WHERE id = ?", [user_id])
    return {"message": "已停用"}


# ============================================================
# P - 阈值配置
# ============================================================
class ThresholdUpdate(BaseModel):
    value: float
    color_warning: Optional[str] = None
    color_critical: Optional[str] = None
    is_enabled: Optional[bool] = None


@router.get("/thresholds")
def list_thresholds(_=Depends(get_current_user)):
    """P - 六类预警规则配置；历史阈值不再暴露给前端。"""
    from core.alert_rules import rule_rows
    with sqlite_client.sqlite_session() as db:
        from core.alert_rules import ensure_alert_rule_schema
        ensure_alert_rule_schema(db)
        return rule_rows(db)


@router.put("/thresholds/{threshold_key}")
def update_threshold(
    threshold_key: str,
    req: ThresholdUpdate,
    current_user=Depends(require_roles("admin")),
):
    """P - 修改阈值"""
    from core.alert_rules import RULE_KEYS, ensure_alert_rule_schema
    if threshold_key not in RULE_KEYS:
        raise HTTPException(status_code=404, detail="该阈值已停用")
    with sqlite_client.sqlite_session() as db:
        ensure_alert_rule_schema(db)

    sets = ["value = ?"]
    params = [req.value]
    if req.color_warning is not None:
        sets.append("color_warning = ?")
        params.append(req.color_warning)
    if req.color_critical is not None:
        sets.append("color_critical = ?")
        params.append(req.color_critical)
    if req.is_enabled is not None:
        sets.append("is_enabled = ?")
        params.append(req.is_enabled)

    sets.append("updated_by = ?")
    params.append(current_user["id"])
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(threshold_key)

    sqlite_client.execute(
        f"UPDATE thresholds SET {', '.join(sets)} WHERE threshold_key = ?", params
    )
    return {"message": "阈值已更新"}


# ============================================================
# N - 导入批次
# ============================================================
@router.get("/imports")
def list_imports(
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _=Depends(get_current_user),
):
    """N - 导入批次列表"""
    conditions = ["1=1"]
    params = []
    if status:
        conditions.append("status = ?")
        params.append(status)

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    total = sqlite_client.query_one(
        f"SELECT COUNT(*) as cnt FROM import_batches WHERE {where}", params
    )["cnt"]
    rows = sqlite_client.query_all(
        f"""
        SELECT * FROM import_batches
        WHERE {where}
        ORDER BY started_at DESC
        LIMIT ? OFFSET ?
        """,
        params + [page_size, offset],
    )
    return {"total": total, "page": page, "page_size": page_size, "items": rows}


# ============================================================
# Q/R - 全局设置
# ============================================================
@router.get("/settings")
def get_settings(_=Depends(get_current_user)):
    """R - 全局设置"""
    rows = sqlite_client.query_all("SELECT * FROM settings ORDER BY key")
    return {row["key"]: row["value"] for row in rows}


class SettingUpdate(BaseModel):
    value: str


@router.put("/settings/{key}")
def update_setting(
    key: str,
    req: SettingUpdate,
    current_user=Depends(require_roles("admin")),
):
    """R - 修改设置"""
    sqlite_client.execute(
        """
        UPDATE settings SET value = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP
        WHERE key = ?
        """,
        [req.value, current_user["id"], key],
    )
    return {"message": "设置已更新"}
