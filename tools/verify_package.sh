#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f manifest.sha256 ]]; then
  echo "缺少 manifest.sha256"
  exit 2
fi

shasum -a 256 -c manifest.sha256

shopt -s nullglob
parquets=(data/ods_transactions_all-*.parquet)
if [[ ! -f data/merchant.duckdb || ${#parquets[@]} -ne 1 ]]; then
  echo "分析运行包不完整：需要 merchant.duckdb 和且仅一个内容寻址 Parquet"
  exit 3
fi
bundle_bytes=$(( $(wc -c < data/merchant.duckdb) + $(wc -c < "${parquets[0]}") ))
if (( bundle_bytes > 100 * 1024 * 1024 )); then
  echo "分析运行包超过 100MiB：${bundle_bytes} bytes"
  exit 4
fi
echo "分析运行包: ${bundle_bytes} bytes"

python3 - <<'PY'
import sqlite3
import shutil
import tempfile
from pathlib import Path

root = Path.cwd()
with tempfile.TemporaryDirectory(prefix="yeahpay-biz-check-") as temp:
    check_db = Path(temp) / "biz.db"
    shutil.copy2(root / "data" / "biz.db", check_db)
    db = sqlite3.connect(check_db)
    result = db.execute("PRAGMA integrity_check").fetchone()[0]
    db.close()
if result != "ok":
    raise SystemExit(f"SQLite integrity_check 失败: {result}")
print("SQLite integrity_check: ok")
PY

echo "交接包完整性验证通过"
