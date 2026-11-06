"""
v2 后台管理接口
1. POST /import/transactions  触发ETL脚本
2. POST /users/sync-sales     从DuckDB同步销售到SQLite
3. PUT /users/{id}/password   管理员改别人密码
4. PUT /users/{id}/team       配置团队关系
5. POST /settings/reset       恢复预设
6. GET /users/{id}/merchants  销售名下商户数
"""
import subprocess
import os
import shutil
import threading
import secrets
import uuid
import time
import sys
import duckdb
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from pydantic import BaseModel, field_validator
from typing import Optional
from core import sqlite_client, duckdb_client, auth
from core.data_refresh_audit import (
    compare_entity_snapshots,
    inspect_entity_snapshot,
    public_snapshot,
    reconcile_operational_entities,
)
from core.auth import get_current_user, require_roles
from core.config import settings
from core.duckdb_bundle import (
    bundle_files,
    bundle_fingerprint,
    bundle_size,
    configure_connection,
    external_transaction_file,
)
from core.sql_constants import merchant_binding_where

router = APIRouter(prefix="/v2/admin", tags=["v2-后台管理"])
_publish_lock = threading.Lock()

# 允许的文件扩展名
ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv"}


class PasswordReset(BaseModel):
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, v):
        if len(v) < 6:
            raise ValueError("密码至少6位")
        return v


class TeamConfig(BaseModel):
    supervisor_id: int


def _sanitize_filename(filename: str) -> str:
    """清理文件名，防止路径穿越"""
    # 只保留文件名部分，去掉路径
    safe_name = os.path.basename(filename)
    # 生成唯一文件名避免冲突
    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {ext}，仅支持 {ALLOWED_EXTENSIONS}")
    return f"{int(time.time())}_{uuid.uuid4().hex[:8]}{ext}"


@router.post("/import/transactions")
async def import_transactions(
    file: UploadFile = File(...),
    mode: Literal["append", "replace", "fails_only"] = Query("append", description="append/replace/fails_only"),
    current_user=Depends(require_roles("admin")),
):
    """触发 ETL 脚本导入交易数据"""
    # 安全的文件名
    safe_filename = _sanitize_filename(file.filename or "upload.xlsx")
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / safe_filename

    # 流式写入，避免把整个上传文件一次性读入内存
    bytes_written = 0
    try:
        with open(file_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                bytes_written += len(chunk)
                if bytes_written > settings.MAX_UPLOAD_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件过大，最大 {settings.MAX_UPLOAD_SIZE // 1024 // 1024}MB",
                    )
                f.write(chunk)
    except Exception:
        file_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    # 记录导入批次
    batch_id = sqlite_client.execute(
        """
        INSERT INTO import_batches (filename, file_type, import_mode, status, triggered_by, operator_id)
        VALUES (?, 'daily_transactions', ?, 'running', 'manual', ?)
        """,
        [file.filename, mode, current_user["id"]],
    )

    # 触发现有 ETL 脚本
    etl_script = settings.ETL_SCRIPT_PATH
    if not os.path.exists(etl_script):
        sqlite_client.execute(
            "UPDATE import_batches SET status='failed', error_message=?, finished_at=CURRENT_TIMESTAMP WHERE id=?",
            [f"ETL脚本不存在: {etl_script}", batch_id],
        )
        raise HTTPException(status_code=500, detail=f"ETL脚本不存在: {etl_script}")

    try:
        etl_env = os.environ.copy()
        etl_env["YEAHPAY_IMPORT_MODE"] = mode
        subprocess.Popen(
            [sys.executable, etl_script, str(file_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=etl_env,
        )
    except Exception as e:
        sqlite_client.execute(
            "UPDATE import_batches SET status='failed', error_message=?, finished_at=CURRENT_TIMESTAMP WHERE id=?",
            [str(e), batch_id],
        )
        raise HTTPException(status_code=500, detail=f"ETL 触发失败: {e}")

    return {"batch_id": batch_id, "message": "导入已触发，请稍后查看状态"}


@router.post("/users/sync-sales")
def sync_sales(_=Depends(require_roles("admin"))):
    """从 DuckDB merchant_trans_30 同步销售名单到 SQLite users 表"""
    # 获取 DuckDB 里的所有业务员
    db_sales = duckdb_client.query_all(
        """
        SELECT DISTINCT 业务员 AS sales_name
        FROM merchant_trans_30
        WHERE 业务员 IS NOT NULL AND 业务员 != ''
        ORDER BY 业务员
        """
    )

    # 获取已有的销售
    existing = sqlite_client.query_all("SELECT username FROM users WHERE role IN ('sales', 'supervisor')")
    existing_names = {u["username"] for u in existing}

    created = []
    for s in db_sales:
        name = s["sales_name"]
        if name not in existing_names:
            temporary_password = secrets.token_urlsafe(12)
            user_id = sqlite_client.execute(
                """
                INSERT INTO users (username, password_hash, display_name, role, must_change_pwd)
                VALUES (?, ?, ?, 'sales', 1)
                """,
                [name, auth.hash_password(temporary_password), name],
            )
            sqlite_client.execute(
                "INSERT OR REPLACE INTO user_scopes (user_id, scope_type, scope_value) VALUES (?, 'sales', ?)",
                [user_id, name],
            )
            sqlite_client.execute(
                "INSERT OR IGNORE INTO user_scope_bindings (user_id, scope_type, scope_value) VALUES (?, 'sales', ?)",
                [user_id, name],
            )
            created.append({"id": user_id, "username": name, "temporary_password": temporary_password})

    return {"created_count": len(created), "created": created, "message": f"新增 {len(created)} 个销售账号；临时密码仅在本次响应显示"}


@router.put("/users/{user_id}/password")
def reset_user_password(
    user_id: int,
    req: PasswordReset,
    _=Depends(require_roles("admin")),
):
    """管理员重置其他用户密码"""
    user = sqlite_client.query_one("SELECT id FROM users WHERE id = ?", [user_id])
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    sqlite_client.execute(
        "UPDATE users SET password_hash = ?, must_change_pwd = 1 WHERE id = ?",
        [auth.hash_password(req.new_password), user_id],
    )
    return {"message": "密码已重置，用户下次登录需改密"}


@router.put("/users/{user_id}/team")
def config_team(
    user_id: int,
    req: TeamConfig,
    _=Depends(require_roles("admin")),
):
    """配置团队关系（设置主管）"""
    supervisor = sqlite_client.query_one(
        "SELECT id, role FROM users WHERE id = ?", [req.supervisor_id]
    )
    if not supervisor or supervisor["role"] not in ("admin", "supervisor"):
        raise HTTPException(status_code=400, detail="主管不存在或角色不符")

    sqlite_client.execute(
        "UPDATE users SET supervisor_id = ? WHERE id = ?",
        [req.supervisor_id, user_id],
    )
    return {"message": "团队关系已更新"}


@router.get("/users/{user_id}/merchants")
def user_merchants(user_id: int, _=Depends(require_roles("admin"))):
    """获取销售名下商户数"""
    user = sqlite_client.query_one("SELECT display_name, role FROM users WHERE id = ?", [user_id])
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    bindings = sqlite_client.query_all(
        "SELECT scope_type, scope_value FROM user_scope_bindings WHERE user_id = ?", [user_id]
    )
    if not bindings:
        legacy = sqlite_client.query_one("SELECT scope_type, scope_value FROM user_scopes WHERE user_id = ?", [user_id])
        bindings = [legacy] if legacy else []
    if user["role"] == "admin" or any(b.get("scope_type") == "all" for b in bindings):
        result = duckdb_client.query_one(
            f"SELECT COUNT(*) AS cnt FROM merchants m WHERE {merchant_binding_where('m')}"
        )
    else:
        agent_ids = [b["scope_value"] for b in bindings if b.get("scope_type") == "agent"]
        sales_names = [b["scope_value"] for b in bindings if b.get("scope_type") == "sales"]
        parts = []
        params = []
        if agent_ids:
            placeholders = ",".join("?" for _ in agent_ids)
            parts.append(f"CAST(agent_id AS VARCHAR) IN ({placeholders})")
            params.extend(agent_ids)
        if sales_names:
            placeholders = ",".join("?" for _ in sales_names)
            parts.append(
                "CAST(id AS VARCHAR) IN (SELECT DISTINCT CAST(商户编号 AS VARCHAR) "
                f"FROM merchant_trans_30 WHERE 业务员 IN ({placeholders}))"
            )
            params.extend(sales_names)
        if not parts:
            result = {"cnt": 0}
        else:
            result = duckdb_client.query_one(
                "SELECT COUNT(*) AS cnt FROM merchants m WHERE "
                + merchant_binding_where("m")
                + " AND ("
                + " OR ".join(parts)
                + ")",
                params,
            )

    # 团队成员数
    team_count = sqlite_client.query_one(
        "SELECT COUNT(*) as cnt FROM users WHERE supervisor_id = ?", [user_id]
    )["cnt"]

    return {
        "merchant_count": result["cnt"],
        "team_count": team_count,
    }


@router.post("/settings/reset")
def reset_settings(current_user=Depends(require_roles("admin"))):
    """恢复默认设置"""
    defaults = {
        "theme_primary_color": "#2B6CB0",
        "theme_success_color": "#22C55E",
        "theme_warning_color": "#F59E0B",
        "theme_error_color": "#FF4D4F",
        "theme_mode": "light",
        "system_name": "立得香港商户交易后台",
        "system_name_en": "HK Orblead Merchant Admin",
    }
    for key, value in defaults.items():
        sqlite_client.execute(
            """
            INSERT INTO settings (key, value, updated_by) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_by=excluded.updated_by, updated_at=CURRENT_TIMESTAMP
            """,
            [key, value, current_user["id"]],
        )
    return {"message": "已恢复默认设置", "defaults": defaults}


@router.post("/regenerate-alerts")
def regenerate_alerts(current_user=Depends(require_roles("admin"))):
    """重新计算预警——删除今日旧数据，按最新阈值重新扫描 DuckDB"""
    import sys

    # 把 scripts/ 目录加入 path
    scripts_dir = str(settings.BACKEND_DIR / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    from generate_alerts import generate as run_generate

    try:
        result = run_generate(force=True)
        return {
            "success": True,
            "thresholds": result.get("thresholds", {}),
            "scanned": result["scanned"],
            "matched": result.get("matched", 0),
            "inserted": result["inserted"],
            "updated": result.get("updated", 0),
            "stale_closed": result.get("stale_closed", 0),
            "total": result["total"],
            "status_breakdown": result["status_breakdown"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"预警重新計算失敗: {str(e)}")

def _candidate_path() -> Path:
    if settings.DUCKDB_CANDIDATE_PATH:
        return Path(settings.DUCKDB_CANDIDATE_PATH).expanduser().resolve()
    active = Path(settings.DUCKDB_PATH).expanduser().resolve()
    root = active.parent.parent if active.parent.name == "active" else active.parent
    return root / "candidate" / active.name


def _backup_dir() -> Path:
    if settings.DUCKDB_BACKUP_DIR:
        return Path(settings.DUCKDB_BACKUP_DIR).expanduser().resolve()
    active = Path(settings.DUCKDB_PATH).expanduser().resolve()
    root = active.parent.parent if active.parent.name == "active" else active.parent
    return root / "backup"


def _inspect_duckdb(path: Path) -> dict:
    if not path.is_file():
        return {
            "exists": False, "a_date": "", "b_date": "", "b_all_date": "", "publish_date": "",
            "entity_snapshot": inspect_entity_snapshot(path),
        }
    try:
        entity_snapshot = inspect_entity_snapshot(path)
        if not entity_snapshot.get("ok"):
            stat = path.stat()
            return {
                "exists": True,
                "a_date": "", "b_date": "", "b_all_date": "", "publish_date": "",
                "comparison": None,
                "size": stat.st_size,
                "mtime": int(stat.st_mtime),
                "fingerprint": f"{stat.st_size}:{int(stat.st_mtime)}",
                "entity_snapshot": entity_snapshot,
            }
        conn = duckdb.connect(str(path), read_only=True)
        configure_connection(conn, path)
        a = conn.execute("SELECT MAX(trans_date) FROM transaction_amounts").fetchone()[0]
        b = conn.execute("SELECT MAX(created_at) FROM ods_transactions_all WHERE trans_status='交易成功'").fetchone()[0]
        b_all = conn.execute("SELECT MAX(created_at) FROM ods_transactions_all").fetchone()[0]
        a_date = str(a)[:10] if a else ""
        b_date = str(b)[:10] if b else ""
        b_all_date = str(b_all)[:10] if b_all else ""
        publish_date = min(a_date, b_date) if a_date and b_date else ""
        comparison = None
        if publish_date:
            row = conn.execute("""
                SELECT a.dt FROM (SELECT DISTINCT trans_date AS dt FROM transaction_amounts) a
                JOIN (SELECT DISTINCT CAST(created_at AS DATE) AS dt FROM ods_transactions_all
                      WHERE trans_status='交易成功') b ON a.dt=b.dt
                WHERE a.dt<=? ORDER BY a.dt DESC LIMIT 1
            """, [publish_date]).fetchone()
            if row:
                overlap = str(row[0])[:10]
                a_amt = conn.execute(
                    "SELECT COALESCE(SUM(amount),0) FROM transaction_amounts WHERE trans_date=? AND amount>0",
                    [overlap],
                ).fetchone()[0] or 0
                b_amt = conn.execute(
                    "SELECT COALESCE(SUM(amount),0) FROM ods_transactions_all WHERE CAST(created_at AS DATE)=? AND trans_status='交易成功' AND amount>0",
                    [overlap],
                ).fetchone()[0] or 0
                comparison = {
                    "date": overlap,
                    "a_amount": a_amt,
                    "b_amount": b_amt,
                    "diff_pct": round((b_amt-a_amt)/a_amt*100, 2) if a_amt else 0,
                    "diff_amount": b_amt-a_amt,
                }
        external = external_transaction_file(conn, path)
        conn.close()
        return {
            "exists": True,
            "a_date": a_date,
            "b_date": b_date,
            "b_all_date": b_all_date,
            "publish_date": publish_date,
            "comparison": comparison,
            "size": bundle_size(path),
            "mtime": int(path.stat().st_mtime),
            "fingerprint": bundle_fingerprint(path),
            "external_file": external.name if external else "",
            "external_size": external.stat().st_size if external else 0,
            "entity_snapshot": entity_snapshot,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"候选 DuckDB 校验失败: {exc}")


def _setting(key: str, default=""):
    row = sqlite_client.query_one("SELECT value FROM settings WHERE key=?", [key])
    return row.get("value", default) if row else default


@router.get("/data-refresh-status")
def data_refresh_status(current_user=Depends(require_roles("admin"))):
    """读取候选库状态；没有候选库时只展示当前正式库，禁止发布。"""
    confirmed_date = _setting("data_refresh_date", "未确认")
    candidate = _inspect_duckdb(_candidate_path())
    active = _inspect_duckdb(Path(settings.DUCKDB_PATH).expanduser().resolve())
    sync_value = str(_setting("duckdb_sync_consistent", "")).lower()
    recorded_fingerprint = str(_setting("duckdb_sync_candidate_fingerprint", ""))
    fingerprint_matches = bool(
        candidate["exists"]
        and recorded_fingerprint
        and recorded_fingerprint == candidate.get("fingerprint")
    )
    if not candidate["exists"]:
        consistent = None
        consistency_text = "待上传候选库"
    elif sync_value != "true":
        consistent = False if sync_value == "false" else None
        consistency_text = "不一致" if consistent is False else "待本地校验"
    elif not fingerprint_matches:
        consistent = False
        consistency_text = "校验结果已过期，请重新检查"
    else:
        consistent = True
        consistency_text = "一致"
    source = candidate if candidate["exists"] else active
    publish_date = source.get("publish_date", "")
    needs_refresh = bool(candidate["exists"] and publish_date and publish_date != confirmed_date)
    candidate_validation = candidate.get("entity_snapshot", {})
    entity_changes = compare_entity_snapshots(
        active.get("entity_snapshot", {}),
        candidate_validation,
        settings.DATA_REFRESH_MAX_MERCHANT_REMOVAL_RATIO,
    ) if candidate["exists"] and active["exists"] else {}
    validation_ok = bool(
        candidate["exists"]
        and candidate_validation.get("ok") is True
        and not entity_changes.get("blocked", False)
    )
    can_publish = bool(needs_refresh and consistent is True and validation_ok)
    return {
        "confirmed_date": confirmed_date,
        "a_table": {"label": "A表(近30天汇总 Excel)", "latest_date": source.get("a_date", ""), "source": "transaction_amounts"},
        "b_table": {"label": "B表(交易明细)", "latest_date": source.get("b_date", ""), "all_date": source.get("b_all_date", ""), "source": "ods_transactions_all"},
        "publish_date": publish_date,
        "needs_refresh": needs_refresh,
        "can_publish": can_publish,
        "candidate_source": "candidate" if candidate["exists"] else "active",
        "candidate_exists": candidate["exists"],
        "candidate_size": candidate.get("size", 0),
        "candidate_fingerprint": candidate.get("fingerprint", ""),
        "sync_batch_id": _setting("duckdb_sync_batch_id", ""),
        "overlap_comparison": source.get("comparison"),
        "candidate_validation": public_snapshot(candidate_validation),
        "active_validation": public_snapshot(active.get("entity_snapshot", {})),
        "entity_changes": entity_changes,
        "local_cloud_consistency": {
            "consistent": consistent,
            "checked_at": _setting("duckdb_sync_checked_at", ""),
            "text": consistency_text,
        },
    }


@router.post("/confirm-data-refresh")
def confirm_data_refresh(current_user=Depends(require_roles("admin"))):
    """校验候选库并原子替换正式库；发布设置在文件切换成功后更新。"""
    with _publish_lock:
        active = Path(settings.DUCKDB_PATH).expanduser().resolve()
        candidate = _candidate_path()
        if not candidate.is_file():
            raise HTTPException(status_code=409, detail="没有可发布的候选数据库")
        if str(candidate) == str(active):
            raise HTTPException(status_code=500, detail="候选库不能与正式库使用同一路径")
        recorded_fingerprint = str(_setting("duckdb_sync_candidate_fingerprint", ""))
        try:
            candidate_files = bundle_files(candidate)
            actual_fingerprint = bundle_fingerprint(candidate)
        except (FileNotFoundError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        if (
            str(_setting("duckdb_sync_consistent", "")).lower() != "true"
            or not recorded_fingerprint
            or recorded_fingerprint != actual_fingerprint
        ):
            raise HTTPException(status_code=409, detail="本地与云端一致性尚未通过")
        inspected = _inspect_duckdb(candidate)
        validation = inspected.get("entity_snapshot", {})
        if validation.get("ok") is not True:
            detail = "；".join(validation.get("issues", [])) or "候选库实体完整性校验失败"
            raise HTTPException(status_code=409, detail=detail)
        active_snapshot = inspect_entity_snapshot(active) if active.is_file() else {"ok": True}
        entity_changes = compare_entity_snapshots(
            active_snapshot,
            validation,
            settings.DATA_REFRESH_MAX_MERCHANT_REMOVAL_RATIO,
        ) if active.is_file() else {}
        if entity_changes.get("blocked"):
            raise HTTPException(status_code=409, detail=entity_changes.get("block_reason"))
        publish_date = inspected.get("publish_date", "")
        if not publish_date:
            raise HTTPException(status_code=409, detail="候选库两个数据源没有共同可发布日期")
        confirmed = _setting("data_refresh_date", "")
        if confirmed and confirmed != "未确认" and publish_date <= confirmed:
            raise HTTPException(status_code=409, detail="候选库没有比当前正式版更新的数据")

        active.parent.mkdir(parents=True, exist_ok=True)
        for candidate_file in candidate_files:
            if candidate_file.stat().st_dev != active.parent.stat().st_dev:
                raise HTTPException(status_code=409, detail="候选运行包和正式库必须位于同一磁盘，才能安全切换")
        backup_path = None
        backup_version_dir = None
        active_files = []
        if active.exists():
            backup_dir = _backup_dir()
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_version_dir = backup_dir / time.strftime('%Y%m%d-%H%M%S')
            backup_version_dir.mkdir(parents=True, exist_ok=False)
            try:
                active_files = bundle_files(active)
            except (FileNotFoundError, RuntimeError) as exc:
                raise HTTPException(status_code=409, detail=f"正式运行包不完整：{exc}")
            for source_file in active_files:
                destination = backup_version_dir / source_file.name
                try:
                    os.link(source_file, destination)
                except OSError:
                    shutil.copy2(source_file, destination)
            backup_path = backup_version_dir / active.name

        candidate_external = candidate_files[1] if len(candidate_files) > 1 else None
        active_external = active.parent / candidate_external.name if candidate_external else None
        external_moved = False
        external_reused = False
        database_switched = False
        try:
            if candidate_external:
                if active_external.exists():
                    if active_external.stat().st_size != candidate_external.stat().st_size:
                        raise HTTPException(status_code=409, detail="正式目录存在同名但大小不同的交易明细文件")
                    external_reused = True
                else:
                    os.replace(candidate_external, active_external)
                    external_moved = True

            os.replace(candidate, active)
            database_switched = True
            # 文件切换后、发布日期写入前立即从正式路径执行一次完整检查。
            switched = _inspect_duckdb(active)
            if switched.get("publish_date") != publish_date:
                raise RuntimeError("切换后的正式运行包发布日期校验失败")
            try:
                directory_fd = os.open(str(active.parent), os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except (AttributeError, OSError):
                pass

            sqlite_client.execute(
                """INSERT INTO settings(key,value,updated_by) VALUES('data_refresh_date',?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",
                [publish_date, current_user["id"]],
            )
            batch_id = _setting("duckdb_sync_batch_id", "")
            if batch_id:
                sqlite_client.execute(
                    """INSERT INTO settings(key,value,updated_by) VALUES('data_refresh_batch_id',?,?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",
                    [batch_id, current_user["id"]],
                )
        except Exception:
            # 文件切换成功但发布记录写入失败时，立即恢复原状；否则前端
            # 可能读取到新库、设置却仍显示旧日期，形成难以察觉的半发布。
            if database_switched and active.exists():
                os.replace(active, candidate)
            if backup_path and backup_path.exists():
                os.replace(backup_path, active)
            if external_moved and active_external and active_external.exists():
                os.replace(active_external, candidate_external)
            raise
        if external_reused and candidate_external and candidate_external.exists():
            candidate_external.unlink()
        old_external = active_files[1] if len(active_files) > 1 else None
        if old_external and active_external and old_external != active_external and old_external.exists():
            old_external.unlink()
        post_publish = {"alerts_closed": 0, "pushes_closed": 0, "alerts_regenerated": False}
        post_publish_warnings = []
        try:
            post_publish.update(reconcile_operational_entities(validation.get("_merchant_ids", set())))
        except Exception as exc:
            post_publish_warnings.append(f"旧工作流清理失败：{exc}")
        try:
            from scripts.generate_alerts import generate as generate_alerts
            generation = generate_alerts(force=True)
            post_publish["alerts_regenerated"] = True
            post_publish["alert_matches"] = generation.get("matched", 0)
        except Exception as exc:
            post_publish_warnings.append(f"预警重算失败：{exc}")
        return {
            "confirmed_date": publish_date,
            "published": True,
            "batch_id": batch_id,
            "backup_path": str(backup_path) if backup_path else "",
            "entity_changes": entity_changes,
            "post_publish": post_publish,
            "warnings": post_publish_warnings,
            "message": f"数据已确认更新至 {publish_date}",
        }
