#!/bin/bash
# tradfi-spot 已改为 live-only；本脚本仅保留作一次性清理废弃日线产物。
# 日常请用 run_tradfi_spot_snapshot.sh（每小时快照）。
set -o pipefail
cd /root/data-download || exit 1

VENV=./.venv/bin/python
LOG=/root/data-download/tradfi-spot/_daily.log
DEST=/root/dashboard/public/json
ts() { date '+%Y-%m-%d %H:%M:%S'; }

{
  echo "==================== [$(ts)] tradfi-spot legacy purge ===================="
  echo "[$(ts)] 清理 tradfi-spot-price / tradfi-arb-spread ..."
  $VENV tradfi-spot/_cleanup.py --dashboard "$DEST"
  echo "[$(ts)] 完成（live-only 模式不再跑 Yahoo 日线）"
} >> "$LOG" 2>&1
