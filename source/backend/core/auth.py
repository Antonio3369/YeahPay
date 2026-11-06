"""
JWT 认证 + RBAC 权限
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt  # PyJWT
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from core.config import settings
from core import sqlite_client

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_PREFIX}/auth/login")


def hash_password(password: str) -> str:
    """密码加密"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """验证密码"""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: int, username: str, role: str) -> str:
    """生成 JWT token"""
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.JWT_EXPIRE_HOURS)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """解码 JWT"""
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 已过期，请重新登录",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效",
        )


def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """获取当前登录用户（FastAPI 依赖）"""
    payload = decode_token(token)
    subject = payload.get("sub")
    if not subject:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 缺少用户标识",
        )
    try:
        user_id = int(subject)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 用户标识无效",
        )
    user = sqlite_client.query_one(
        "SELECT id, username, display_name, role, supervisor_id, email, phone, is_active "
        "FROM users WHERE id = ?",
        [user_id],
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
        )
    if not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已停用",
        )
    bindings = sqlite_client.query_all(
        "SELECT scope_type, scope_value FROM user_scope_bindings WHERE user_id = ? "
        "ORDER BY scope_type, scope_value",
        [user_id],
    )
    if not bindings:
        # 兼容旧版本的单范围账号。
        legacy_scope = sqlite_client.query_one(
            "SELECT scope_type, scope_value FROM user_scopes WHERE user_id = ?",
            [user_id],
        )
        if legacy_scope and legacy_scope.get("scope_value"):
            bindings = [legacy_scope]
        elif user["role"] == "sales":
            # 兼容最早期只用 display_name 作为销售范围的账号。
            bindings = [{"scope_type": "sales", "scope_value": user["display_name"]}]

    user["scope_bindings"] = bindings
    user["scope_agents"] = [str(b["scope_value"]) for b in bindings if b["scope_type"] == "agent"]
    user["scope_sales"] = [str(b["scope_value"]) for b in bindings if b["scope_type"] == "sales"]
    if user["role"] == "admin":
        user["scope_type"] = "all"
        user["scope_value"] = None
    elif len(bindings) == 1:
        user["scope_type"] = bindings[0]["scope_type"]
        user["scope_value"] = bindings[0]["scope_value"]
    else:
        user["scope_type"] = "custom"
        user["scope_value"] = None
    user["scope_label"] = (
        "全部机构" if user["scope_type"] == "all"
        else ", ".join(user["scope_agents"] + user["scope_sales"])
        or "未配置数据范围"
    )
    user["token_payload"] = payload
    return user


def resolve_user_scope(current_user: dict, agent_id=None, sales_person=None):
    """返回当前账号真正允许使用的数据范围。

    管理员可以使用前端传入的临时视角；其他账号完全忽略前端参数，
    使用账号在 user_scopes 中绑定的固定范围。
    """
    if current_user.get("role") == "admin":
        return agent_id, sales_person
    scope_agents = current_user.get("scope_agents") or []
    scope_sales = current_user.get("scope_sales") or []
    # 旧接口仍返回单值；支持多绑定的接口应使用 resolve_scope_values。
    if len(scope_agents) == 1 and not scope_sales:
        return scope_agents[0], None
    if len(scope_sales) == 1 and not scope_agents:
        return None, scope_sales[0]
    return None, None


def resolve_scope_values(current_user: dict, agent_id=None, sales_person=None):
    """返回真正生效的多范围数据权限。

    返回 (agent_ids, sales_names)。管理员使用前端临时视角；其他角色完全
    忽略前端传参，只使用账号绑定的固定范围。
    """
    if current_user.get("role") == "admin":
        return (
            [str(agent_id)] if agent_id else [],
            [str(sales_person)] if sales_person else [],
        )
    return (
        [str(v) for v in (current_user.get("scope_agents") or []) if str(v).strip()],
        [str(v) for v in (current_user.get("scope_sales") or []) if str(v).strip()],
    )


def require_roles(*roles: str):
    """RBAC 角色装饰器
    用法:
        @router.get("/users", dependencies=[Depends(require_roles("admin"))])
        def list_users(): ...
    """

    def role_checker(current_user: dict = Depends(get_current_user)) -> dict:
        if current_user["role"] not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足，需要角色: {', '.join(roles)}",
            )
        return current_user

    return role_checker
