import tempfile
import unittest
import sqlite3
from pathlib import Path
from unittest.mock import patch

import duckdb

from core.data_refresh_audit import (
    compare_entity_snapshots,
    inspect_entity_snapshot,
    reconcile_operational_entities,
)
from api.scope import list_agents, list_sales


def make_database(path: Path, merchants, assignments):
    conn = duckdb.connect(str(path))
    conn.execute("CREATE TABLE merchants(id VARCHAR, name VARCHAR, agent_id VARCHAR, agent_name VARCHAR, status VARCHAR, bound_sn VARCHAR, bound_qr_code VARCHAR)")
    conn.execute('CREATE TABLE merchant_trans_30("商户编号" VARCHAR, "业务员" VARCHAR)')
    conn.execute("CREATE TABLE transaction_amounts(merchant_id VARCHAR, trans_date DATE, amount DOUBLE)")
    conn.execute("CREATE TABLE ods_transactions_all(merchant_id VARCHAR, created_at TIMESTAMP, trans_status VARCHAR, amount DOUBLE)")
    conn.executemany("INSERT INTO merchants VALUES (?,?,?,?,?,?,?)", merchants)
    conn.executemany("INSERT INTO merchant_trans_30 VALUES (?,?)", assignments)
    conn.close()


class DataRefreshAuditTest(unittest.TestCase):
    def test_detects_add_remove_and_stale_binding(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            active_path = Path(temp_dir) / "active.duckdb"
            candidate_path = Path(temp_dir) / "candidate.duckdb"
            make_database(active_path, [
                ("1", "旧商户", "A1", "旧代理", "正常", "SN1", ""),
                ("2", "保留商户", "A1", "旧代理", "正常", "SN2", ""),
            ], [("1", "旧销售"), ("2", "旧销售")])
            make_database(candidate_path, [
                ("2", "保留商户", "A1", "旧代理", "正常", "SN2", ""),
                ("3", "新商户", "A2", "新代理", "正常", "SN3", ""),
            ], [("2", "旧销售"), ("3", "新销售")])
            active = inspect_entity_snapshot(active_path)
            candidate = inspect_entity_snapshot(candidate_path)
            bindings = [{"user_id": 9, "username": "old", "scope_type": "sales", "scope_value": "已删除销售"}]
            with patch("core.data_refresh_audit.sqlite_client.query_all", return_value=bindings):
                changes = compare_entity_snapshots(active, candidate, 0.75)

            self.assertTrue(active["ok"])
            self.assertTrue(candidate["ok"])
            self.assertEqual(changes["merchant_added"], 1)
            self.assertEqual(changes["merchant_removed"], 1)
            self.assertEqual(changes["agent_added"], 1)
            self.assertEqual(changes["sales_added"], 1)
            self.assertEqual(changes["stale_binding_count"], 1)
            self.assertFalse(changes["blocked"])

    def test_blocks_suspicious_mass_deletion(self):
        active = {
            "_merchant_ids": {str(i) for i in range(10)}, "_merchant_names": {},
            "_agent_ids": set(), "_sales_names": set(),
        }
        candidate = {
            "_merchant_ids": {"1"}, "_merchant_names": {},
            "_agent_ids": set(), "_sales_names": set(),
        }
        with patch("core.data_refresh_audit.sqlite_client.query_all", return_value=[]):
            changes = compare_entity_snapshots(active, candidate, 0.20)
        self.assertTrue(changes["blocked"])
        self.assertEqual(changes["merchant_removed"], 9)

    def test_closes_only_open_workflows_for_removed_merchants(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sqlite_path = Path(temp_dir) / "biz.db"
            db = sqlite3.connect(sqlite_path)
            db.execute("CREATE TABLE alerts(id INTEGER PRIMARY KEY, merchant_id TEXT, status TEXT, updated_at TIMESTAMP)")
            db.execute("CREATE TABLE merchant_pushes(id INTEGER PRIMARY KEY, merchant_id TEXT, status TEXT, updated_at TIMESTAMP)")
            db.executemany("INSERT INTO alerts VALUES (?,?,?,NULL)", [
                (1, "keep", "following"), (2, "remove", "following"), (3, "remove", "resolved")
            ])
            db.executemany("INSERT INTO merchant_pushes VALUES (?,?,?,NULL)", [
                (1, "keep", "following"), (2, "remove", "following"), (3, "remove", "resolved")
            ])
            db.commit()
            db.close()
            with patch("core.sqlite_client.settings.SQLITE_PATH", str(sqlite_path)):
                result = reconcile_operational_entities({"keep"})
            db = sqlite3.connect(sqlite_path)
            alert_statuses = dict(db.execute("SELECT id,status FROM alerts").fetchall())
            push_statuses = dict(db.execute("SELECT id,status FROM merchant_pushes").fetchall())
            db.close()
            self.assertEqual(result, {"alerts_closed": 1, "pushes_closed": 1})
            self.assertEqual(alert_statuses, {1: "following", 2: "ignored", 3: "resolved"})
            self.assertEqual(push_statuses, {1: "following", 2: "ignored", 3: "resolved"})

    def test_scope_options_follow_new_database_without_hardcoded_entities(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            candidate_path = Path(temp_dir) / "candidate.duckdb"
            sqlite_path = Path(temp_dir) / "biz.db"
            make_database(candidate_path, [
                ("3", "新商户", "A2", "新代理", "正常", "SN3", ""),
            ], [("3", "新销售")])
            db = sqlite3.connect(sqlite_path)
            db.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)")
            db.execute(
                "INSERT INTO settings(key, value) VALUES ('data_refresh_date', '2026-08-15')"
            )
            db.commit()
            db.close()
            with patch("core.duckdb_client.settings.DUCKDB_PATH", str(candidate_path)), \
                 patch("core.duckdb_client.settings.DUCKDB_RELEASE_GATE_ENABLED", False), \
                 patch("core.sqlite_client.settings.SQLITE_PATH", str(sqlite_path)):
                agents = list_agents(None)
                sales = list_sales(None)
            self.assertEqual([row["agent_id"] for row in agents], ["A2"])
            self.assertEqual([row["sales_name"] for row in sales], ["新销售"])


if __name__ == "__main__":
    unittest.main()
