import os
import tempfile
import unittest
from pathlib import Path

import duckdb

from core.duckdb_bundle import (
    bundle_files,
    bundle_fingerprint,
    configure_connection,
)


class DuckDBBundleTest(unittest.TestCase):
    def test_relative_parquet_is_resolved_from_database_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parquet = root / "ods_transactions_all-0123456789abcdef.parquet"
            database = root / "merchant.duckdb"
            memory = duckdb.connect(":memory:")
            memory.execute("CREATE TABLE tx AS SELECT 1 AS merchant_id")
            memory.execute(f"COPY tx TO '{parquet}' (FORMAT PARQUET)")
            memory.close()
            connection = duckdb.connect(str(database))
            configure_connection(connection, database)
            connection.execute(
                f"CREATE VIEW ods_transactions_all AS SELECT * FROM read_parquet('{parquet.name}')"
            )
            connection.close()

            files = bundle_files(database)
            self.assertEqual(files, [database.resolve(), parquet.resolve()])
            first = bundle_fingerprint(database)
            stat = parquet.stat()
            os.utime(parquet, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
            self.assertNotEqual(first, bundle_fingerprint(database))

    def test_external_file_cannot_escape_database_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root.parent / "ods_transactions_all-0123456789abcdef.parquet"
            database = root / "merchant.duckdb"
            memory = duckdb.connect(":memory:")
            memory.execute("CREATE TABLE tx AS SELECT 1 AS merchant_id")
            memory.execute(f"COPY tx TO '{outside}' (FORMAT PARQUET)")
            memory.close()
            connection = duckdb.connect(str(database))
            connection.execute(
                "CREATE VIEW ods_transactions_all AS "
                f"SELECT * FROM read_parquet('{outside}')"
            )
            connection.close()
            try:
                with self.assertRaises(RuntimeError):
                    bundle_files(database)
            finally:
                outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
