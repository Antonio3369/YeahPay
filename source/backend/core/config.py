"""
FastAPI 后端配置
"""
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    # === 路径 ===
    # config.py 在 backend/core/config.py
    # parent = core/, parent.parent = backend/
    BACKEND_DIR: Path = Path(__file__).resolve().parent.parent
    BASE_DIR: Path = BACKEND_DIR.parent

    # DuckDB 只读分析库（现有，不动）
    # 默认指向项目包 05-database/merchant.duckdb，可通过 .env 覆盖
    DUCKDB_PATH: str = str(BASE_DIR / "05-database" / "merchant.duckdb")
    # 待发布数据库与备份目录。留空时按 active/、candidate/、backup/ 兄弟目录推导。
    DUCKDB_CANDIDATE_PATH: str = ""
    DUCKDB_BACKUP_DIR: str = ""
    DUCKDB_RELEASE_GATE_ENABLED: bool = True
    # 候选库相对正式库一次删除有效商户的最大比例。超过时视为可能上传了截断快照，禁止发布。
    DATA_REFRESH_MAX_MERCHANT_REMOVAL_RATIO: float = 0.20

    # SQLite 业务运营库（新建）
    SQLITE_PATH: str = str(BACKEND_DIR / "data" / "biz.db")

    # === JWT ===
    # 生产环境必须通过 .env 设置 JWT_SECRET
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24

    # === 服务 ===
    APP_NAME: str = "立得香港商户交易后台"
    API_PREFIX: str = "/api/v1"
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]
    ENVIRONMENT: Literal["development", "production"] = "development"

    # === DuckDB 查询 ===
    DUCKDB_MEMORY_LIMIT: str = "2GB"
    DUCKDB_THREADS: int = 4

    # === 文件上传 ===
    UPLOAD_DIR: str = str(BACKEND_DIR / "data" / "uploads")
    MAX_UPLOAD_SIZE: int = 100 * 1024 * 1024  # 100MB

    # === ETL 脚本路径 ===
    # 本地开发：openclaw_auto_worker.py 未包含在本包中，可通过 .env 覆盖；
    # 云上部署可指向 01-hk-data/deploy/cloud_etl.py
    ETL_SCRIPT_PATH: str = str(BASE_DIR / "01-hk-data" / "deploy" / "cloud_etl.py")

    @model_validator(mode="after")
    def validate_security_settings(self):
        if not self.JWT_SECRET:
            raise ValueError("JWT_SECRET 未配置，请在 .env 中设置随机密钥")
        if self.ENVIRONMENT == "production":
            if len(self.JWT_SECRET) < 32 or self.JWT_SECRET == "change-this-to-a-random-secret":
                raise ValueError("生产环境 JWT_SECRET 必须是至少 32 位随机密钥")
            if "*" in self.CORS_ORIGINS:
                raise ValueError("生产环境不允许 CORS_ORIGINS 使用 *")
        return self


settings = Settings()
