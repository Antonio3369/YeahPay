"""
SQLite 读写客户端
用途: 连接 biz.db 执行业务数据读写（账号/预警/阈值等）

并发策略:
- WAL 模式支持并发读 + 单写
- 每次请求创建新连接，避免线程共享问题
- write_timeout 防止写锁等待超时
"""
import sqlite3
import threading
from contextlib import contextmanager
from core.config import settings

# 写锁，确保同一时间只有一个写操作
_write_lock = threading.Lock()


def ensure_scope_schema():
    """为已有 biz.db 补齐账号数据范围表（幂等迁移）。"""
    with _write_lock:
        conn = get_sqlite_connection()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_scopes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL UNIQUE,
                    scope_type TEXT NOT NULL CHECK(scope_type IN ('all', 'agent', 'sales')),
                    scope_value TEXT,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    CHECK (
                        (scope_type = 'all' AND (scope_value IS NULL OR scope_value = '')) OR
                        (scope_type IN ('agent', 'sales') AND scope_value IS NOT NULL AND scope_value != '')
                    )
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_scope_bindings (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL,
                    scope_type  TEXT NOT NULL CHECK(scope_type IN ('agent', 'sales')),
                    scope_value TEXT NOT NULL,
                    updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    UNIQUE (user_id, scope_type, scope_value)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scope_bindings_user ON user_scope_bindings(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scope_bindings_value ON user_scope_bindings(scope_type, scope_value)")
            conn.execute(
                """
                INSERT OR IGNORE INTO user_scope_bindings (user_id, scope_type, scope_value)
                SELECT user_id, scope_type, scope_value
                FROM user_scopes
                WHERE scope_type IN ('agent', 'sales') AND scope_value IS NOT NULL AND scope_value != ''
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO user_scopes (user_id, scope_type, scope_value)
                SELECT id, 'all', NULL FROM users WHERE role = 'admin'
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS merchant_pushes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    merchant_id TEXT NOT NULL,
                    merchant_name TEXT,
                    push_note TEXT,
                    pushed_by INTEGER NOT NULL,
                    pushed_to INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'following'
                        CHECK(status IN ('following', 'resolved', 'ignored')),
                    accepted_at TIMESTAMP,
                    touch_type TEXT CHECK(touch_type IN ('online', 'offline') OR touch_type IS NULL),
                    resolved BOOLEAN NOT NULL DEFAULT 0,
                    resolution_note TEXT,
                    resolved_by INTEGER,
                    resolved_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (pushed_by) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (pushed_to) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (resolved_by) REFERENCES users(id) ON DELETE SET NULL
                )
                """
            )
            try:
                conn.execute("ALTER TABLE merchant_pushes ADD COLUMN push_note TEXT")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pushes_recipient_status ON merchant_pushes(pushed_to, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pushes_sender_status ON merchant_pushes(pushed_by, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pushes_merchant_status ON merchant_pushes(merchant_id, status)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS followup_records (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_type   TEXT NOT NULL CHECK(source_type IN ('alert', 'push')),
                    source_id     INTEGER NOT NULL,
                    merchant_id   TEXT NOT NULL,
                    merchant_name TEXT,
                    user_id       INTEGER NOT NULL,
                    touch_type    TEXT CHECK(touch_type IN ('online', 'offline') OR touch_type IS NULL),
                    result        TEXT NOT NULL DEFAULT 'following'
                                  CHECK(result IN ('following', 'resolved', 'unreachable', 'other')),
                    note          TEXT NOT NULL DEFAULT '',
                    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_followup_records_source ON followup_records(source_type, source_id, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_followup_records_merchant ON followup_records(merchant_id, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_followup_records_user ON followup_records(user_id, created_at)")
            # 兼容旧数据：将原有预警跟进记录幂等迁移到统一复盘记录表。
            conn.execute(
                """
                INSERT INTO followup_records
                    (source_type, source_id, merchant_id, merchant_name, user_id, touch_type, result, note, created_at)
                SELECT
                    'alert', f.alert_id, a.merchant_id, a.merchant_name, f.user_id,
                    CASE f.followup_type WHEN 'visit' THEN 'offline' WHEN 'call' THEN 'online' WHEN 'email' THEN 'online' ELSE NULL END,
                    CASE WHEN a.status = 'resolved' AND f.id = (
                        SELECT MAX(f2.id) FROM alert_followups f2 WHERE f2.alert_id = f.alert_id
                    ) THEN 'resolved' ELSE 'following' END,
                    f.content, f.created_at
                FROM alert_followups f
                JOIN alerts a ON a.id = f.alert_id
                WHERE NOT EXISTS (
                    SELECT 1 FROM followup_records r
                    WHERE r.source_type = 'alert' AND r.source_id = f.alert_id
                      AND r.user_id = f.user_id AND r.created_at = f.created_at AND r.note = f.content
                )
                """
            )
            # 兼容旧数据：将已有人工推送的解决记录纳入复盘。
            conn.execute(
                """
                INSERT INTO followup_records
                    (source_type, source_id, merchant_id, merchant_name, user_id, touch_type, result, note, created_at)
                SELECT
                    'push', p.id, p.merchant_id, p.merchant_name, COALESCE(p.resolved_by, p.pushed_to),
                    p.touch_type, CASE WHEN p.status = 'resolved' THEN 'resolved' ELSE 'following' END,
                    COALESCE(p.resolution_note, ''), COALESCE(p.resolved_at, p.accepted_at, p.updated_at)
                FROM merchant_pushes p
                WHERE p.accepted_at IS NOT NULL
                  AND (p.resolution_note IS NOT NULL OR p.status = 'resolved')
                  AND NOT EXISTS (
                    SELECT 1 FROM followup_records r
                    WHERE r.source_type = 'push' AND r.source_id = p.id
                )
                """
            )
            conn.executemany(
                """
                INSERT OR IGNORE INTO thresholds
                    (threshold_key, display_name, description, value, unit, color_warning, color_critical)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("silent_max_days", "沉睡最长绑定天数", "绑定不超过该天数的商户归入沉睡范围", 180, "天", "", ""),
                    ("silent_amount", "沉睡历史交易额", "沉睡商户历史累计交易额低于该值", 300, "HKD", "", ""),
                    ("risky_days", "高危绑定天数", "绑定超过该天数且满足其他条件的商户归入高危", 180, "天", "", ""),
                    ("risky_amount", "高危历史交易额", "高危商户历史累计交易额低于该值", 100, "HKD", "", ""),
                    ("risky_lookback_days", "高危无交易天数", "高危商户在该天数内没有正金额交易", 90, "天", "", ""),
                ],
            )
            from core.alert_rules import ensure_alert_rule_schema
            ensure_alert_rule_schema(conn)
            conn.commit()
        finally:
            conn.close()


def get_sqlite_connection():
    """获取 SQLite 连接（每次新建，WAL 模式支持并发读）"""
    conn = sqlite3.connect(
        settings.SQLITE_PATH,
        check_same_thread=False,
        timeout=10.0,  # 写锁等待 10 秒
        isolation_level=None,  # 自动提交模式，由我们手动控制事务
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")  # busy timeout 10s
    return conn


@contextmanager
def sqlite_session():
    """SQLite 会话上下文管理器，自动提交/回滚

    读操作：直接使用，无锁
    写操作：通过 _write_lock 串行化，避免 "database is locked" 错误

    用法:
        with sqlite_session() as db:
            db.execute("INSERT INTO alerts (...) VALUES (...)")
    """
    conn = get_sqlite_connection()
    try:
        conn.execute("BEGIN")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def query_all(sql: str, params=None):
    """查询，返回 list[dict]"""
    with sqlite_session() as db:
        cursor = db.execute(sql, params or [])
        return [dict(row) for row in cursor.fetchall()]


def query_one(sql: str, params=None):
    """查询，返回单个 dict"""
    with sqlite_session() as db:
        cursor = db.execute(sql, params or [])
        row = cursor.fetchone()
        return dict(row) if row else None


def execute(sql: str, params=None) -> int:
    """执行写操作，返回 lastrowid（使用写锁保证并发安全）"""
    with _write_lock:
        with sqlite_session() as db:
            cursor = db.execute(sql, params or [])
            return cursor.lastrowid


def execute_many(sql: str, params_list) -> int:
    """批量执行，返回受影响行数"""
    with _write_lock:
        with sqlite_session() as db:
            cursor = db.executemany(sql, params_list)
            return cursor.rowcount
