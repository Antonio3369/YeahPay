#!/usr/bin/env python3
"""按同一份交易快照生成六类商户预警。"""

import sys
import sqlite3
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from core.config import settings
from core import duckdb_client
from core.alert_rules import ensure_alert_rule_schema
from core.sql_constants import merchant_binding_where

SQLITE_PATH = Path(settings.SQLITE_PATH)
SUCCESS = "t.trans_status = '交易成功' AND COALESCE(t.amount, 0) > 0"
ATTEMPT = "t.trans_status IN ('交易成功', '交易失败')"


def get_threshold(db):
    return {
        r["threshold_key"]: float(r["value"])
        for r in db.execute(
            "SELECT threshold_key, value FROM thresholds WHERE is_enabled=1"
        ).fetchall()
    }


def scan_all_rules(thresholds):
    """一次扫描最近60天交易，返回 merchant_id -> 命中的规则列表。"""
    inactive_days = max(0, int(thresholds["inactive_days"]))
    dormant_days = max(0, int(thresholds["dormant_days"]))
    weekly_decline = max(0.0, float(thresholds["weekly_decline_pct"]))
    monthly_decline = max(0.0, float(thresholds["monthly_decline_pct"]))
    failure_ratio = max(0.0, float(thresholds["failure_ratio_pct"]))
    weekly_spike = max(0.0, float(thresholds["weekly_spike_pct"]))

    conn = duckdb_client.get_duckdb_connection()
    sql = f"""
        WITH base AS (
            SELECT CAST(m.id AS VARCHAR) AS merchant_id,
                   COALESCE(m.short_name, m.name, CAST(m.id AS VARCHAR)) AS merchant_name,
                   m.agent_name,
                   CAST(m.first_bound_time AS DATE) AS bound_date,
                   COALESCE(s.last_trans_date, CAST(m.last_trans_time AS DATE)) AS last_trans_date,
                   COALESCE(s.total_volume, 0) AS history_amount
            FROM merchants m
            LEFT JOIN ads_merchant_status_snapshots s
              ON s.merchant_id = m.id
             AND s.snapshot_date = (
                 SELECT MAX(snapshot_date)
                 FROM ads_merchant_status_snapshots
                 WHERE CAST(snapshot_date AS DATE) <= report_date()
             )
            WHERE {merchant_binding_where('m')}
        ), tx AS (
            SELECT CAST(t.merchant_id AS VARCHAR) AS merchant_id,
                COALESCE(SUM(CASE WHEN {SUCCESS}
                    AND t.created_at >= report_cutoff() - INTERVAL 7 DAY
                    THEN t.amount ELSE 0 END), 0) AS week_current_amount,
                COALESCE(SUM(CASE WHEN {SUCCESS}
                    AND t.created_at >= report_cutoff() - INTERVAL 14 DAY
                    AND t.created_at < report_cutoff() - INTERVAL 7 DAY
                    THEN t.amount ELSE 0 END), 0) AS week_previous_amount,
                COALESCE(SUM(CASE WHEN {SUCCESS}
                    AND t.created_at >= report_cutoff() - INTERVAL 30 DAY
                    THEN t.amount ELSE 0 END), 0) AS month_current_amount,
                COALESCE(SUM(CASE WHEN {SUCCESS}
                    AND t.created_at >= report_cutoff() - INTERVAL 60 DAY
                    AND t.created_at < report_cutoff() - INTERVAL 30 DAY
                    THEN t.amount ELSE 0 END), 0) AS month_previous_amount,
                COUNT(CASE WHEN {ATTEMPT}
                    AND t.created_at >= report_cutoff() - INTERVAL 30 DAY THEN 1 END) AS attempt_count,
                COUNT(CASE WHEN t.trans_status = '交易失败'
                    AND t.created_at >= report_cutoff() - INTERVAL 30 DAY THEN 1 END) AS failed_count
            FROM ods_transactions_all t
            WHERE t.created_at >= report_cutoff() - INTERVAL 60 DAY
              AND t.created_at < report_cutoff()
            GROUP BY 1
        )
        SELECT b.*, COALESCE(tx.week_current_amount, 0) AS week_current_amount,
               COALESCE(tx.week_previous_amount, 0) AS week_previous_amount,
               COALESCE(tx.month_current_amount, 0) AS month_current_amount,
               COALESCE(tx.month_previous_amount, 0) AS month_previous_amount,
               COALESCE(tx.attempt_count, 0) AS attempt_count,
               COALESCE(tx.failed_count, 0) AS failed_count
        FROM base b LEFT JOIN tx USING (merchant_id)
    """
    rows = conn.execute(sql).fetchall()
    columns = [item[0] for item in conn.description]
    conn.close()
    report_date = duckdb_client.get_reporting_date()
    matches = {}

    for row in rows:
        item = dict(zip(columns, row))
        tags = []
        bound_date = item["bound_date"]
        bound_age = (report_date - bound_date).days if bound_date else None
        if bound_age is not None and 0 <= bound_age <= inactive_days and item["history_amount"] < 100:
            tags.append({"key": "inactive_days", "metric": float(item["history_amount"]), "threshold": inactive_days,
                         "detail": f"啟用未滿{inactive_days}天，累計交易額低於HK$100"})
        if item["last_trans_date"] and (report_date - item["last_trans_date"]).days > dormant_days:
            dormant_value = (report_date - item["last_trans_date"]).days
            tags.append({"key": "dormant_days", "metric": dormant_value, "threshold": dormant_days,
                         "detail": f"最後交易距今{dormant_value}天"})

        def change_pct(current, previous):
            return ((current - previous) / previous * 100) if previous > 0 else None

        week_pct = change_pct(item["week_current_amount"], item["week_previous_amount"])
        month_pct = change_pct(item["month_current_amount"], item["month_previous_amount"])
        if week_pct is not None and week_pct < -weekly_decline:
            tags.append({"key": "weekly_decline_pct", "metric": round(abs(week_pct), 1), "threshold": weekly_decline,
                         "detail": f"近7天交易額下降{abs(week_pct):.1f}%"})
        if month_pct is not None and month_pct < -monthly_decline:
            tags.append({"key": "monthly_decline_pct", "metric": round(abs(month_pct), 1), "threshold": monthly_decline,
                         "detail": f"近30天交易額下降{abs(month_pct):.1f}%"})
        ratio = (item["failed_count"] / item["attempt_count"] * 100) if item["attempt_count"] else 0
        if ratio > failure_ratio:
            tags.append({"key": "failure_ratio_pct", "metric": round(ratio, 1), "threshold": failure_ratio,
                         "detail": f"近30天交易失敗比例{ratio:.1f}%"})
        if week_pct is not None and week_pct > weekly_spike:
            tags.append({"key": "weekly_spike_pct", "metric": round(week_pct, 1), "threshold": weekly_spike,
                         "detail": f"近7天交易額上升{week_pct:.1f}%"})
        if tags:
            matches[item["merchant_id"]] = {**item, "tags": tags}
    return matches


def count_eligible_merchants():
    """返回本次规则扫描实际覆盖的已绑定、未注销商户数。"""
    conn = duckdb_client.get_duckdb_connection()
    try:
        return conn.execute(
            f"""
            SELECT COUNT(*)
            FROM merchants
            WHERE {merchant_binding_where('')}
            """
        ).fetchone()[0]
    finally:
        conn.close()


def _description(item):
    return "；".join(tag["detail"] for tag in item["tags"])


def generate(force: bool = False):
    db = sqlite3.connect(str(SQLITE_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    ensure_alert_rule_schema(db)
    thresholds = get_threshold(db)
    scanned = count_eligible_merchants()
    matches = scan_all_rules(thresholds)
    snapshot_date = duckdb_client.get_reporting_date().isoformat()

    current_ids = set(matches)
    if current_ids:
        placeholders = ",".join("?" for _ in current_ids)
        stale_closed = db.execute(
            f"""UPDATE alerts SET status='ignored', updated_at=CURRENT_TIMESTAMP
                 WHERE status='unfollowed' AND merchant_id NOT IN ({placeholders})""",
            list(current_ids),
        ).rowcount
    else:
        stale_closed = db.execute(
            "UPDATE alerts SET status='ignored', updated_at=CURRENT_TIMESTAMP WHERE status='unfollowed'"
        ).rowcount

    inserted = 0
    updated = 0
    for item in matches.values():
        merchant_id = item["merchant_id"]
        active_rows = db.execute(
            "SELECT id, alert_type, status FROM alerts WHERE merchant_id=? AND status IN ('unfollowed','following')",
            [merchant_id],
        ).fetchall()
        following = any(row["status"] == "following" for row in active_rows)
        existing_by_type = {row["alert_type"]: row for row in active_rows}
        for tag in item["tags"]:
            row = existing_by_type.get(tag["key"])
            title = f"商戶「{item['merchant_name']}」命中{tag['detail']}"
            description = _description(item)
            if row:
                db.execute(
                    """UPDATE alerts SET merchant_name=?, title=?, description=?, metric_value=?,
                       threshold_value=?, snapshot_date=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    [item["merchant_name"], title, description, tag["metric"], tag["threshold"], snapshot_date, row["id"]],
                )
                updated += 1
            else:
                db.execute(
                    """INSERT INTO alerts
                       (merchant_id,merchant_name,alert_type,alert_level,title,description,
                        metric_value,threshold_value,snapshot_date,status)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    [merchant_id, item["merchant_name"], tag["key"], "warning", title,
                     description, tag["metric"], tag["threshold"], snapshot_date,
                     "following" if following else "unfollowed"],
                )
                inserted += 1

    db.commit()
    total = db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    status_breakdown = {
        row["status"]: row["c"]
        for row in db.execute("SELECT status, COUNT(DISTINCT merchant_id) c FROM alerts GROUP BY status").fetchall()
    }
    db.close()
    return {"thresholds": thresholds, "scanned": scanned, "matched": len(matches), "inserted": inserted,
            "updated": updated, "stale_closed": stale_closed, "total": total,
            "status_breakdown": status_breakdown, "snapshot_date": snapshot_date}


if __name__ == "__main__":
    print(generate())
