import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import duckdb

from core import duckdb_client, sqlite_client
from core.config import settings


class TimeAuditTest(unittest.TestCase):
    def _biz_db(self, path: Path, value: Optional[str]):
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)")
        if value is not None:
            db.execute("INSERT INTO settings(key, value) VALUES ('data_refresh_date', ?)", [value])
        db.commit()
        db.close()

    def test_release_gate_hides_future_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            duck_path = temp / "merchant.duckdb"
            biz_path = temp / "biz.db"
            self._biz_db(biz_path, "2026-08-15")
            db = duckdb.connect(str(duck_path))
            db.execute("CREATE TABLE ods_transactions_all(merchant_id VARCHAR, created_at TIMESTAMP, amount DOUBLE)")
            db.executemany(
                "INSERT INTO ods_transactions_all VALUES (?, ?, ?)",
                [("1", "2026-08-15 12:00:00", 100), ("1", "2026-08-16 12:00:00", 200)],
            )
            db.close()

            with patch.object(settings, "DUCKDB_PATH", str(duck_path)), \
                 patch.object(settings, "DUCKDB_RELEASE_GATE_ENABLED", True), \
                 patch.object(settings, "ENVIRONMENT", "production"), \
                 patch.object(sqlite_client.settings, "SQLITE_PATH", str(biz_path)):
                conn = duckdb_client.get_duckdb_connection()
                try:
                    latest = conn.execute("SELECT MAX(CAST(created_at AS DATE)) FROM ods_transactions_all").fetchone()[0]
                finally:
                    conn.close()
            self.assertEqual(str(latest), "2026-08-15")

    def test_missing_release_date_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            biz_path = Path(temp_dir) / "biz.db"
            self._biz_db(biz_path, None)
            with patch.object(sqlite_client.settings, "SQLITE_PATH", str(biz_path)):
                with self.assertRaises(RuntimeError):
                    duckdb_client.get_reporting_date()

    def test_frontend_and_sales_api_use_six_month_window(self):
        root = Path(__file__).resolve().parents[2]
        frontend_path = root / "02-yeepay-miniprogram/pages/sales-performance/sales-performance.js"
        backend_path = root / "04-backend/api/v2/sales_performance.py"
        if not frontend_path.exists():
            frontend_path = root / "miniprogram/pages/sales-performance/sales-performance.js"
            backend_path = root / "backend/api/v2/sales_performance.py"
        frontend = frontend_path.read_text()
        backend = backend_path.read_text()
        self.assertIn("existing-trend?months=6", frontend)
        self.assertIn("trend?months=6", frontend)
        self.assertIn("window_months = months - 1", backend)


if __name__ == "__main__":
    unittest.main()
