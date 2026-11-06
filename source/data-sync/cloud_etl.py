#!/usr/bin/env python3
"""
cloud_etl.py — 云端 ETL 脚本
接收增量 Excel，导入云端 DuckDB，刷新聚合层

被 sync_hk_db.sh 调用：
  /opt/yeahpay/etl/venv/bin/python3 \\
    /opt/yeahpay/etl/scripts/cloud_etl.py \\
    /opt/yeahpay/etl/data/uploads/20260728_example.xlsx
"""
import os
import sys
import argparse
import traceback
from datetime import datetime

# ============================================================
# 1. 路径初始化：从 CLOUD_PROJECT 定位 backend 模块
# ============================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))       # .../scripts/
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)                     # .../etl-engine/
BACKEND_DIR = os.path.join(PROJECT_DIR, "backend")

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
# 也把 PROJECT_DIR 加入，以支持 "from backend.xxx" 导入方式
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)


def log(msg: str):
    """带时间戳的日志输出"""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def run_cloud_etl(excel_path: str):
    """
    云端 ETL 主流程：
    1. 校验 Excel
    2. 导入 DuckDB (Bulk Upsert)
    3. 刷新数仓聚合层 (DWS/ADS)
    不生成报表、不发送邮件（本地方做）
    """
    log(f"========================================")
    log(f"☁️  云端 ETL 启动")
    log(f"📂 文件: {excel_path}")
    log(f"🐤 DuckDB: {os.path.join(BACKEND_DIR, 'data', 'merchant.duckdb')}")
    log(f"========================================")

    # --------------------------------------------------
    # Step 1: 校验 + 导入
    # --------------------------------------------------
    log("🔍 [1/3] 审计并导入 Excel ...")
    try:
        from backend.database import SessionLocal
        from backend.service.excel_processor import ExcelProcessor
    except ImportError as e:
        log(f"❌ 无法导入 backend 模块: {e}")
        log(f"   sys.path = {sys.path[:3]}")
        sys.exit(2)

    db = SessionLocal()
    try:
        processor = ExcelProcessor(db)

        # 1a. 合法性校验
        audit = processor.validate_excel(excel_path)
        if not audit.get("is_valid", False):
            log("🚨 数据审计未通过:")
            for w in audit.get("warnings", []):
                log(f"   - [{w.get('title', '?')}] {', '.join(w.get('details', []))}")
            sys.exit(3)

        log("   ✅ 审计通过，开始入库 ...")

        # 1b. 入库
        def progress(step, pct, detail):
            if pct in (0, 50, 100):
                log(f"   [{step}] {pct}%: {detail}")

        result = processor.process_excel(
            excel_path,
            filename=os.path.basename(excel_path),
            on_progress=progress,
        )
        if result.get("status") != "success":
            log(f"❌ 入库失败: {result.get('detail')}")
            sys.exit(4)

        log("   ✅ 数据导入完成")
    finally:
        db.close()

    # --------------------------------------------------
    # Step 2: 刷新聚合层
    # --------------------------------------------------
    log("🔄 [2/3] 刷新数仓聚合层 (DWS/ADS) ...")
    db = SessionLocal()
    try:
        from backend.service.warehouse_service import WarehouseService
        ws = WarehouseService(db)
        ws.refresh_all_layers()
        log("   ✅ 聚合层刷新完成")
    except Exception as e:
        log(f"❌ 聚合刷新失败: {e}")
        traceback.print_exc()
        sys.exit(5)
    finally:
        db.close()

    # --------------------------------------------------
    # Step 3: 验证行数
    # --------------------------------------------------
    log("✅ [3/3] 验证 ...")
    try:
        import duckdb
        db_path = os.path.join(BACKEND_DIR, "data", "merchant.duckdb")
        conn = duckdb.connect(db_path, read_only=True)
        tx_count = conn.execute(
            "SELECT COUNT(*) FROM ods_transactions_all"
        ).fetchone()[0]
        merchant_count = conn.execute(
            "SELECT COUNT(*) FROM merchants"
        ).fetchone()[0]
        conn.close()
        log(f"   ods_transactions_all: {tx_count:,} 行")
        log(f"   merchants:            {merchant_count:,} 行")
    except Exception as e:
        log(f"⚠️ 行数验证跳过: {e}")

    log(f"🎉 [DONE] 云端 ETL 完成")
    return True


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="云服务器 ETL：导入 Excel 到 DuckDB"
    )
    parser.add_argument(
        "excel_path",
        help="Excel 文件路径 (通常位于 data/uploads/)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.excel_path):
        print(f"❌ 文件不存在: {args.excel_path}")
        sys.exit(1)

    try:
        run_cloud_etl(args.excel_path)
    except SystemExit:
        raise
    except Exception:
        print(f"💥 未预期异常:")
        traceback.print_exc()
        sys.exit(99)
