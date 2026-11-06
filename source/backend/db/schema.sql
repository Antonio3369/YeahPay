-- ============================================================
-- hk-orblead-admin 业务运营数据库 (SQLite)
-- 路径: backend/data/biz.db
-- 用途: 存放销售后台专属数据（账号/预警/阈值等）
-- 注意: 分析数据走 merchant.duckdb（只读），这里只存业务运营数据
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ============================================================
-- 1. users — 后台账号
-- ============================================================
-- DuckDB 里有旧 users 表（40行，字段不全），这里新建独立表
-- 角色: admin（管理员）/ supervisor（主管）/ sales（销售）
-- 主管-成员关系: sales.supervisor_id → users.id
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT    NOT NULL UNIQUE,
    password_hash   TEXT    NOT NULL,
    display_name    TEXT    NOT NULL,
    role            TEXT    NOT NULL CHECK(role IN ('admin', 'supervisor', 'sales')),
    supervisor_id   INTEGER,
    email           TEXT,
    phone           TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT 1,
    must_change_pwd BOOLEAN NOT NULL DEFAULT 0,  -- 首次登录强制改密
    last_login_at   TIMESTAMP,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (supervisor_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_supervisor ON users(supervisor_id);

-- ============================================================
-- 1.1 user_scopes — 账号固定数据范围
-- role 决定功能权限，user_scopes 决定能看哪些数据
-- scope_type: all / agent / sales
-- ============================================================
CREATE TABLE IF NOT EXISTS user_scopes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL UNIQUE,
    scope_type  TEXT NOT NULL CHECK(scope_type IN ('all', 'agent', 'sales')),
    scope_value TEXT,
    updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CHECK (
        (scope_type = 'all' AND (scope_value IS NULL OR scope_value = '')) OR
        (scope_type IN ('agent', 'sales') AND scope_value IS NOT NULL AND scope_value != '')
    )
);

CREATE INDEX IF NOT EXISTS idx_user_scopes_user ON user_scopes(user_id);
CREATE INDEX IF NOT EXISTS idx_user_scopes_value ON user_scopes(scope_type, scope_value);

-- 1.2 user_scope_bindings — 账号数据范围多选绑定
-- 一个代理商账号可绑定一个代理商、多个销售，也可只绑定销售。
CREATE TABLE IF NOT EXISTS user_scope_bindings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    scope_type  TEXT NOT NULL CHECK(scope_type IN ('agent', 'sales')),
    scope_value TEXT NOT NULL,
    updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE (user_id, scope_type, scope_value)
);

CREATE INDEX IF NOT EXISTS idx_scope_bindings_user ON user_scope_bindings(user_id);
CREATE INDEX IF NOT EXISTS idx_scope_bindings_value ON user_scope_bindings(scope_type, scope_value);

-- 初始管理员（密码 admin，bcrypt hash，首次登录强制改密）
-- password_hash 会在后端启动时自动生成（避免硬编码 hash 过期）
INSERT OR IGNORE INTO users (username, password_hash, display_name, role, must_change_pwd)
VALUES ('admin', '__INITIAL_ADMIN_HASH__', '超级管理员', 'admin', 1);

-- ============================================================
-- 2. alerts — 预警记录
-- ============================================================
-- 来源: ETL 完成后按 thresholds 配置自动生成
-- 状态: unfollowed（未跟进）/ following（跟进中）/ resolved（已解决）/ ignored（已忽略）
CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant_id     TEXT    NOT NULL,          -- 对应 DuckDB merchants.id
    merchant_name   TEXT,                       -- 冗余存储，避免 join
    alert_type      TEXT    NOT NULL,           -- silent / decline / high_risk / mcc_deviation
    alert_level     TEXT    NOT NULL DEFAULT 'warning',  -- info / warning / critical
    title           TEXT    NOT NULL,
    description     TEXT,
    metric_value    DOUBLE,                     -- 触发指标值（如环比下降百分比）
    threshold_value DOUBLE,                     -- 阈值
    snapshot_date   DATE    NOT NULL,           -- 预警快照日期
    status          TEXT    NOT NULL DEFAULT 'unfollowed'
                    CHECK(status IN ('unfollowed', 'following', 'resolved', 'ignored')),
    assigned_to     INTEGER,                    -- 指派给哪个销售
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (assigned_to) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_alerts_merchant ON alerts(merchant_id);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
CREATE INDEX IF NOT EXISTS idx_alerts_snapshot ON alerts(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_alerts_type ON alerts(alert_type);
CREATE INDEX IF NOT EXISTS idx_alerts_assigned ON alerts(assigned_to);

-- ============================================================
-- 3. alert_followups — 预警跟进记录
-- ============================================================
CREATE TABLE IF NOT EXISTS alert_followups (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,           -- 跟进人
    content         TEXT    NOT NULL,           -- 跟进内容
    followup_type   TEXT    NOT NULL DEFAULT 'note'
                    CHECK(followup_type IN ('note', 'call', 'visit', 'email')),
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_followups_alert ON alert_followups(alert_id);
CREATE INDEX IF NOT EXISTS idx_followups_user ON alert_followups(user_id);

-- ============================================================
-- 3.1 merchant_pushes — 商户人工推送与跟进
-- ============================================================
-- 推送与系统 alerts 分开存储：一个商户可以被多次推送给不同账号。
CREATE TABLE IF NOT EXISTS merchant_pushes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant_id     TEXT    NOT NULL,
    merchant_name   TEXT,
    push_note       TEXT,
    pushed_by       INTEGER NOT NULL,
    pushed_to       INTEGER NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'following'
                    CHECK(status IN ('following', 'resolved', 'ignored')),
    accepted_at     TIMESTAMP,
    touch_type      TEXT
                    CHECK(touch_type IN ('online', 'offline') OR touch_type IS NULL),
    resolved        BOOLEAN NOT NULL DEFAULT 0,
    resolution_note TEXT,
    resolved_by     INTEGER,
    resolved_at     TIMESTAMP,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (pushed_by) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (pushed_to) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (resolved_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_pushes_recipient_status ON merchant_pushes(pushed_to, status);
CREATE INDEX IF NOT EXISTS idx_pushes_sender_status ON merchant_pushes(pushed_by, status);
CREATE INDEX IF NOT EXISTS idx_pushes_merchant_status ON merchant_pushes(merchant_id, status);

-- ============================================================
-- 3.2 followup_records — 统一复盘跟进记录
-- ============================================================
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
);

CREATE INDEX IF NOT EXISTS idx_followup_records_source ON followup_records(source_type, source_id, created_at);
CREATE INDEX IF NOT EXISTS idx_followup_records_merchant ON followup_records(merchant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_followup_records_user ON followup_records(user_id, created_at);

-- ============================================================
-- 4. thresholds — 阈值配置
-- ============================================================
-- 预警页的六项规则配置；旧阈值仍可留在已有数据库中作历史兼容，
-- 但不再由前端展示。
CREATE TABLE IF NOT EXISTS thresholds (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    threshold_key   TEXT    NOT NULL UNIQUE,
    display_name    TEXT    NOT NULL,           -- 中文显示名
    description     TEXT,
    value           DOUBLE  NOT NULL,           -- 阈值数值
    unit            TEXT    NOT NULL DEFAULT '', -- 单位（天/%/分/HKD）
    color_warning   TEXT    NOT NULL DEFAULT '', -- 标黄阈值
    color_critical  TEXT    NOT NULL DEFAULT '', -- 标红阈值
    is_enabled      BOOLEAN NOT NULL DEFAULT 1,
    updated_by      INTEGER,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL
);

-- 初始阈值数据
INSERT OR IGNORE INTO thresholds (threshold_key, display_name, description, value, unit, color_warning, color_critical) VALUES
    ('inactive_days',       '未激活',         '激活未满该天数累计成功交易额仍低于HK$100', 14,  '天', '', ''),
    ('dormant_days',        '已沉默',         '最后一次成功交易距今超过该天数',           180, '天', '', ''),
    ('weekly_decline_pct',  '交易周环比下降', '最近7天交易额较前7天下降超过该比例',       50,  '%', '', ''),
    ('monthly_decline_pct', '交易月环比下降', '最近30天交易额较前30天下降超过该比例',     50,  '%', '', ''),
    ('failure_ratio_pct',   '交易失败',       '最近30天失败交易笔数占有效交易笔数超过该比例', 50, '%', '', ''),
    ('weekly_spike_pct',    '交易异动',       '最近7天交易额较前7天上升超过该比例',        300, '%', '', '');

-- ============================================================
-- 5. import_batches — 导入批次审计
-- ============================================================
-- 记录每次 ETL 导入的执行情况（对应截图 N）
CREATE TABLE IF NOT EXISTS import_batches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filename        TEXT    NOT NULL,           -- 导入的文件名
    file_type       TEXT    NOT NULL,           -- daily_transactions / monthly_merchant_stats
    import_mode     TEXT    NOT NULL DEFAULT 'append'
                    CHECK(import_mode IN ('append', 'replace', 'fails_only')),
    row_count       INTEGER NOT NULL DEFAULT 0,
    success_count   INTEGER NOT NULL DEFAULT 0,
    fail_count      INTEGER NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT 'running'
                    CHECK(status IN ('running', 'success', 'failed', 'rolled_back')),
    error_message   TEXT,
    started_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at     TIMESTAMP,
    triggered_by    TEXT    NOT NULL DEFAULT 'auto'
                    CHECK(triggered_by IN ('auto', 'manual')),
    operator_id     INTEGER,                    -- 手动触发时的操作人
    FOREIGN KEY (operator_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_import_status ON import_batches(status);
CREATE INDEX IF NOT EXISTS idx_import_started ON import_batches(started_at);

-- ============================================================
-- 6. merchant_quotas — 商户额度（暂留空）
-- ============================================================
-- 用户决策 1: 先建空表，后续补上传逻辑
-- 前端"额度%"列暂显示 "—"
CREATE TABLE IF NOT EXISTS merchant_quotas (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant_id         TEXT    NOT NULL UNIQUE,   -- 对应 DuckDB merchants.id
    merchant_name       TEXT,
    swipe_daily_limit   DOUBLE,                     -- 刷卡单日限额 (HKD)
    swipe_single_limit  DOUBLE,                     -- 刷卡单笔限额 (HKD)
    swipe_monthly_limit DOUBLE,                     -- 刷卡单月限额 (HKD)
    scan_daily_limit    DOUBLE,                     -- 扫码单日限额 (HKD)
    scan_single_limit   DOUBLE,                     -- 扫码单笔限额 (HKD)
    scan_monthly_limit  DOUBLE,                     -- 扫码单月限额 (HKD)
    updated_by          INTEGER,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_quotas_merchant ON merchant_quotas(merchant_id);

-- ============================================================
-- 7. settings — 全局设置
-- ============================================================
-- 存放主题色、系统名称等 KV 配置（对应截图 R）
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    description TEXT,
    updated_by  INTEGER,
    updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL
);

-- 初始设置数据
INSERT OR IGNORE INTO settings (key, value, description) VALUES
    ('theme_primary_color', '#1677ff', '主题主色'),
    ('theme_success_color', '#52c41a', '主题成功色'),
    ('theme_warning_color', '#faad14', '主题警告色'),
    ('theme_error_color',   '#ff4d4f', '主题错误色'),
    ('system_name',         '立得香港商户交易后台', '系统名称'),
    ('system_name_en',      'HK Orblead Merchant Admin', '系统英文名');
