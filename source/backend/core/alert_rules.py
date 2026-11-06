"""预警规则定义与 SQLite 阈值迁移。"""

ALERT_RULES = [
    {
        "key": "inactive_days",
        "display_name": "未激活",
        "description": "激活未满该天数，累计成功交易额仍低于 HK$100",
        "default": 14,
        "unit": "天",
    },
    {
        "key": "dormant_days",
        "display_name": "已沉默",
        "description": "最后一次成功交易距今超过该天数",
        "default": 180,
        "unit": "天",
    },
    {
        "key": "weekly_decline_pct",
        "display_name": "交易周环比下降",
        "description": "最近7天交易额较前7天下降超过该比例",
        "default": 50,
        "unit": "%",
    },
    {
        "key": "monthly_decline_pct",
        "display_name": "交易月环比下降",
        "description": "最近30天交易额较前30天下降超过该比例",
        "default": 50,
        "unit": "%",
    },
    {
        "key": "failure_ratio_pct",
        "display_name": "交易失败",
        "description": "最近30天失败交易笔数占有效交易笔数超过该比例",
        "default": 50,
        "unit": "%",
    },
    {
        "key": "weekly_spike_pct",
        "display_name": "交易异动",
        "description": "最近7天交易额较前7天上升超过该比例",
        "default": 300,
        "unit": "%",
    },
]

RULE_KEYS = {rule["key"] for rule in ALERT_RULES}


def ensure_alert_rule_schema(conn):
    """为已有 biz.db 增加新规则，旧配置保留但不再显示在阈值页面。"""
    old_monthly = conn.execute(
        "SELECT value FROM thresholds WHERE threshold_key='decline_pct'"
    ).fetchone()
    old_monthly_value = float(old_monthly[0]) if old_monthly else 50

    for rule in ALERT_RULES:
        value = old_monthly_value if rule["key"] == "monthly_decline_pct" else rule["default"]
        conn.execute(
            """
            INSERT OR IGNORE INTO thresholds
                (threshold_key, display_name, description, value, unit, color_warning, color_critical, is_enabled)
            VALUES (?, ?, ?, ?, ?, '', '', 1)
            """,
            [rule["key"], rule["display_name"], rule["description"], value, rule["unit"]],
        )
        # 规则文案属于协议的一部分，升级已有数据库时同步更新文案，但不覆盖管理员已保存的数值。
        conn.execute(
            "UPDATE thresholds SET display_name=?, description=?, unit=? WHERE threshold_key=?",
            [rule["display_name"], rule["description"], rule["unit"], rule["key"]],
        )

    # 早期版本把周下降默认值写成30%，新规则默认值固定为50%。仅修正旧默认值，
    # 后续管理员自行修改过的值不会在每次启动时被覆盖。
    conn.execute(
        "UPDATE thresholds SET value=50 WHERE threshold_key='weekly_decline_pct' AND value=30"
    )

    # 兼容历史 alert_type，后续统一按新规则 key 处理。
    conn.execute(
        "UPDATE alerts SET alert_type='monthly_decline_pct' WHERE alert_type IN ('decline', 'monthly_decline')"
    )

    # 历史版本可能已经为同一商户生成重复的月下降记录，只保留最早一条活动记录。
    rows = conn.execute(
        """SELECT id, merchant_id, alert_type FROM alerts
           WHERE status IN ('unfollowed','following')
           ORDER BY id"""
    ).fetchall()
    seen = set()
    for row in rows:
        identity = (str(row[1]), row[2])
        if identity in seen:
            conn.execute(
                "UPDATE alerts SET status='ignored', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                [row[0]],
            )
        else:
            seen.add(identity)


def rule_rows(conn):
    """按固定顺序返回六条当前配置，避免旧阈值混入前端。"""
    rows = {
        row["threshold_key"]: dict(row)
        for row in conn.execute(
            "SELECT * FROM thresholds WHERE threshold_key IN ({})".format(
                ",".join("?" for _ in ALERT_RULES)
            ),
            list(RULE_KEYS),
        ).fetchall()
    }
    return [rows[rule["key"]] for rule in ALERT_RULES if rule["key"] in rows]
