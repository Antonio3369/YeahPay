#!/bin/bash
# ================================================================
# sync_hk_db.sh — 本地+云端 DuckDB 同步编排脚本
# ================================================================
# openclaw 对话触发，自动完成: 本地ETL → 云端ETL → 一致性验证
#
# 用法:
#   sync_hk_db.sh <excel文件路径>
#
# 例子:
#   sync_hk_db.sh ~/Downloads/境外收单_近30天_2026-07-24.xlsx
# ================================================================
set -e

# ==== 配置区 ====
# 必须由 OpenClaw 运行环境显式提供，避免把个人路径和登录信息写进脚本。
: "${CLOUD_HOST:?请设置 CLOUD_HOST，例如 ubuntu@server}"
: "${LOCAL_PROJECT:?请设置 LOCAL_PROJECT，即本地数据仓库根目录}"
CLOUD_PROJECT="${CLOUD_PROJECT:-/opt/yeahpay/etl}"
CLOUD_VENV="${CLOUD_PROJECT}/venv/bin/python3"
CLOUD_ETL="${CLOUD_PROJECT}/scripts/cloud_etl.py"
CLOUD_UPLOAD_DIR="${CLOUD_PROJECT}/data/uploads"
CLOUD_FULL_DUCKDB="${CLOUD_FULL_DUCKDB:-${CLOUD_PROJECT}/backend/data/merchant.duckdb}"
CLOUD_DUCKDB="${CLOUD_DUCKDB:-/opt/yeahpay/data/candidate/merchant.duckdb}"
CLOUD_BIZ_DB="${CLOUD_BIZ_DB:-/opt/yeahpay/backend/data/biz.db}"

LOCAL_WORKER="${LOCAL_WORKER:-${LOCAL_PROJECT}/backend/scripts/openclaw_auto_worker.py}"
LOCAL_VENV="${LOCAL_VENV:-${LOCAL_PROJECT}/backend/venv/bin/python3}"
LOCAL_DUCKDB="${LOCAL_DUCKDB:-${LOCAL_PROJECT}/backend/data/merchant.duckdb}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERIFY_SCRIPT="${SCRIPT_DIR}/verify_duckdb_sync.py"
REMOTE_VERIFY_SCRIPT="${CLOUD_PROJECT}/scripts/verify_duckdb_sync.py"
RUNTIME_BUILDER="${SCRIPT_DIR}/build_runtime_bundle.py"
REMOTE_RUNTIME_BUILDER="${CLOUD_PROJECT}/scripts/build_runtime_bundle.py"
MARK_SYNC_SCRIPT="${SCRIPT_DIR}/mark_sync_verified.py"
REMOTE_MARK_SYNC_SCRIPT="${CLOUD_PROJECT}/scripts/mark_sync_verified.py"

LOG_FILE="$HOME/scripts/sync_hk_db_$(date +%Y%m%d_%H%M%S).log"

EXCEL="$1"

# ==== 参数检查 ====
if [ -z "$EXCEL" ]; then
    echo "❌ 用法: sync_hk_db.sh <excel文件路径>"
    exit 1
fi
if [ ! -f "$EXCEL" ]; then
    echo "❌ 文件不存在: $EXCEL"
    exit 1
fi

EXCEL_NAME="$(basename "$EXCEL")"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
CLOUD_EXCEL="${CLOUD_UPLOAD_DIR}/${TIMESTAMP}_${EXCEL_NAME}"

mkdir -p "$(dirname "$LOG_FILE")"

echo "" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
echo " HK 商户数据库同步" | tee -a "$LOG_FILE"
echo " 时间: $(date)" | tee -a "$LOG_FILE"
echo " 文件: $EXCEL_NAME" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

# ================================================================
# [1/4] 本地 ETL
# ================================================================
echo "" | tee -a "$LOG_FILE"
echo "━━━ [1/4] 本地 ETL ━━━" | tee -a "$LOG_FILE"

if [ ! -f "$LOCAL_WORKER" ]; then
    echo "  ❌ 本地 ETL 脚本不存在: $LOCAL_WORKER" | tee -a "$LOG_FILE"
    exit 1
fi

echo "  脚本: openclaw_auto_worker.py" | tee -a "$LOG_FILE"

if $LOCAL_VENV "$LOCAL_WORKER" "$EXCEL" 2>&1 | tee -a "$LOG_FILE"; then
    echo "  ✅ 本地 ETL 成功" | tee -a "$LOG_FILE"
else
    echo "  ❌ 本地 ETL 失败！中止。
（云端不会受影响——如果之前云端已更新，旧数据仍然可用）" | tee -a "$LOG_FILE"
    exit 1
fi

# ================================================================
# [2/4] 云端 ETL
# ================================================================
echo "" | tee -a "$LOG_FILE"
echo "━━━ [2/4] 云端 ETL ━━━" | tee -a "$LOG_FILE"

# 2a. 上传 Excel 到云服务器
echo "  上传: $EXCEL → 云端" | tee -a "$LOG_FILE"
ssh "$CLOUD_HOST" "mkdir -p $CLOUD_UPLOAD_DIR"
scp -q "$EXCEL" "${CLOUD_HOST}:${CLOUD_EXCEL}"
echo "  ✅ 上传完成" | tee -a "$LOG_FILE"

# 2b. 在云服务器上执行 ETL
echo "  执行: cloud_etl.py" | tee -a "$LOG_FILE"
if ssh "$CLOUD_HOST" "$CLOUD_VENV $CLOUD_ETL $CLOUD_EXCEL" 2>&1 | tee -a "$LOG_FILE"; then
    echo "  ✅ 云端 ETL 成功" | tee -a "$LOG_FILE"
else
    echo "  ❌ 云端 ETL 失败！
本地已完成更新，云端未更新。
请排查后重新执行本脚本，云端会自动重跑。" | tee -a "$LOG_FILE"
    exit 1
fi

# ================================================================
# [3/4] 生成小于 100MiB 的云端候选运行包
# ================================================================
echo "" | tee -a "$LOG_FILE"
echo "━━━ [3/4] 生成候选运行包 ━━━" | tee -a "$LOG_FILE"

if [ ! -f "$RUNTIME_BUILDER" ]; then
    echo "  ❌ 运行包生成脚本不存在: $RUNTIME_BUILDER" | tee -a "$LOG_FILE"
    exit 1
fi
ssh "$CLOUD_HOST" "mkdir -p $(dirname "$REMOTE_RUNTIME_BUILDER") $(dirname "$CLOUD_DUCKDB")"
scp -q "$RUNTIME_BUILDER" "${CLOUD_HOST}:${REMOTE_RUNTIME_BUILDER}"
if ssh "$CLOUD_HOST" "$CLOUD_VENV $REMOTE_RUNTIME_BUILDER --source $CLOUD_FULL_DUCKDB --output-dir $(dirname "$CLOUD_DUCKDB") --replace --max-total-mb 100" 2>&1 | tee -a "$LOG_FILE"; then
    echo "  ✅ 候选运行包生成完成" | tee -a "$LOG_FILE"
else
    echo "  ❌ 候选运行包生成失败，正式库未受影响" | tee -a "$LOG_FILE"
    exit 1
fi

# ================================================================
# [4/4] 一致性验证
# ================================================================
echo "" | tee -a "$LOG_FILE"
echo "━━━ [4/4] 一致性验证 ━━━" | tee -a "$LOG_FILE"

if [ ! -f "$VERIFY_SCRIPT" ]; then
    echo "  ❌ 一致性检查脚本不存在: $VERIFY_SCRIPT" | tee -a "$LOG_FILE"
    exit 1
fi

echo "  上传一致性检查脚本到云端..." | tee -a "$LOG_FILE"
ssh "$CLOUD_HOST" "mkdir -p $(dirname "$REMOTE_VERIFY_SCRIPT")"
scp -q "$VERIFY_SCRIPT" "${CLOUD_HOST}:${REMOTE_VERIFY_SCRIPT}"

LOCAL_REPORT="$(mktemp -t yeahpay_local_sync.XXXXXX)"
CLOUD_REPORT="$(mktemp -t yeahpay_cloud_sync.XXXXXX)"
trap 'rm -f "$LOCAL_REPORT" "$CLOUD_REPORT"' EXIT

echo "  正在读取本地 DuckDB 逻辑指纹..." | tee -a "$LOG_FILE"
if ! "$LOCAL_VENV" "$VERIFY_SCRIPT" "$LOCAL_DUCKDB" >"$LOCAL_REPORT" 2>>"$LOG_FILE"; then
    echo "  ❌ 本地 DuckDB 一致性检查失败" | tee -a "$LOG_FILE"
    exit 1
fi

echo "  正在读取云端 DuckDB 逻辑指纹..." | tee -a "$LOG_FILE"
if ! ssh "$CLOUD_HOST" "$CLOUD_VENV $REMOTE_VERIFY_SCRIPT $CLOUD_DUCKDB" >"$CLOUD_REPORT" 2>>"$LOG_FILE"; then
    echo "  ❌ 云端 DuckDB 一致性检查失败" | tee -a "$LOG_FILE"
    exit 1
fi

all_pass=true
echo "" | tee -a "$LOG_FILE"
printf "  %-35s %15s %15s %s\n" "检查项目" "本地" "云端" "状态" | tee -a "$LOG_FILE"
echo "  ---------------------------------------------------------------------------------" | tee -a "$LOG_FILE"

while IFS='|' read -r table local_count local_digest_hi local_digest_lo local_min local_max; do
    cloud_row=$(grep "^${table}|" "$CLOUD_REPORT" || true)
    IFS='|' read -r _ cloud_count cloud_digest_hi cloud_digest_lo cloud_min cloud_max <<< "$cloud_row"
    if [ "$local_count|$local_digest_hi|$local_digest_lo|$local_min|$local_max" = "$cloud_count|$cloud_digest_hi|$cloud_digest_lo|$cloud_min|$cloud_max" ]; then
        icon="✅"
    else
        icon="❌"
        all_pass=false
    fi
    printf "  %-35s %15s %15s %s\n" "$table" "$local_count" "$cloud_count" "$icon" | tee -a "$LOG_FILE"
done < "$LOCAL_REPORT"

echo "" | tee -a "$LOG_FILE"
if [ "$all_pass" = true ]; then
    echo "  ✅ 全部一致 —— 本地与云端逻辑数据一致" | tee -a "$LOG_FILE"
    if [ ! -f "$MARK_SYNC_SCRIPT" ]; then
        echo "  ❌ 同步状态写入脚本不存在: $MARK_SYNC_SCRIPT" | tee -a "$LOG_FILE"
        exit 1
    fi
    scp -q "$MARK_SYNC_SCRIPT" "${CLOUD_HOST}:${REMOTE_MARK_SYNC_SCRIPT}"
    ssh "$CLOUD_HOST" "$CLOUD_VENV $REMOTE_MARK_SYNC_SCRIPT --database $CLOUD_DUCKDB --sqlite $CLOUD_BIZ_DB --batch-id $TIMESTAMP --consistent true" | tee -a "$LOG_FILE"
else
    echo "  ❌ 存在偏差！以下为详细指纹差异：" | tee -a "$LOG_FILE"
    diff -u "$LOCAL_REPORT" "$CLOUD_REPORT" | sed -n '1,160p' | tee -a "$LOG_FILE" || true
fi

echo "" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
echo " 同步结束: $(date)" | tee -a "$LOG_FILE"
echo " 日志: $LOG_FILE" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

if [ "$all_pass" = true ]; then
    exit 0
else
    exit 1
fi
