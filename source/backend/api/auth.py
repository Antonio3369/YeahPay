"""
认证模块 - 登录/改密/当前用户信息
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from core import auth, sqlite_client

router = APIRouter(prefix="/auth", tags=["认证"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest):
    """登录"""
    user = sqlite_client.query_one(
        "SELECT * FROM users WHERE username = ?", [req.username]
    )
    if not user or not auth.verify_password(req.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    if not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已停用",
        )

    # 更新最后登录时间
    sqlite_client.execute(
        "UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?",
        [user["id"]],
    )

    token = auth.create_access_token(user["id"], user["username"], user["role"])
    return LoginResponse(
        access_token=token,
        user={
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "role": user["role"],
            "must_change_pwd": bool(user["must_change_pwd"]),
        },
    )


@router.post("/change-password")
def change_password(
    req: ChangePasswordRequest, current_user=Depends(auth.get_current_user)
):
    """修改密码（首次登录强制改密用）"""
    user = sqlite_client.query_one(
        "SELECT password_hash FROM users WHERE id = ?", [current_user["id"]]
    )
    if not auth.verify_password(req.old_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="原密码错误")

    new_hash = auth.hash_password(req.new_password)
    sqlite_client.execute(
        "UPDATE users SET password_hash = ?, must_change_pwd = 0 WHERE id = ?",
        [new_hash, current_user["id"]],
    )
    return {"message": "密码修改成功"}


@router.get("/me")
def get_me(current_user=Depends(auth.get_current_user)):
    """获取当前用户信息"""
    return current_user
