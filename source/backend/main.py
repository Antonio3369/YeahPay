"""
FastAPI 入口
启动: uvicorn main:app --reload --port 8000
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings
from core import sqlite_client
from api import auth, merchants, alerts, fails, admin, scope, pushes, reviews
from api.v2 import dashboard_extra, team_extra, merchants_extra, alerts_extra, overseas_extra, mastercard_extra, admin_extra, silent_extra, sales_performance, entity_detail

app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# 兼容已经存在的 biz.db，启动时幂等补齐账号数据范围表。
sqlite_client.ensure_scope_schema()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
routers = [
    # v1 原有接口
    auth.router,
    merchants.router,
    alerts.router,
    pushes.router,
    reviews.router,
    fails.router,
    admin.router,
    scope.router,
    # v2 新增接口
    dashboard_extra.router,
    team_extra.router,
    merchants_extra.router,
    alerts_extra.router,
    overseas_extra.router,
    mastercard_extra.router,
    admin_extra.router,
    silent_extra.router,
    sales_performance.router,
    entity_detail.router,
]
for r in routers:
    app.include_router(r, prefix=settings.API_PREFIX)


@app.get("/")
def root():
    return {"message": f"{settings.APP_NAME} API", "docs": "/docs"}


@app.get("/health")
def health():
    return {"status": "ok"}
