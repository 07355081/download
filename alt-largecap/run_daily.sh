#!/bin/bash
# 每天算一次已结束月份的大市值山寨相对比特币。建议 crontab:
#   30 6 * * * /root/data-download/alt-largecap/run_daily.sh
# 算完后发当月日常；更早的过线月份发历史阈值并加特殊警报。
# 当月过线仍由 08:00 看板警报任务发送，避免同一条发两次。
set -o pipefail
cd /root/data-download || exit 1
VENV=./.venv/bin/python
LOG=/root/data-download/alt-largecap/_daily.log
DEST=/root/dashboard/public/json
{
  echo "======== [$(date '+%Y-%m-%d %H:%M:%S')] alt-largecap ========"
  $VENV alt-largecap/download.py
  $VENV copy_to_dashboard.py --only alt-largecap --dest "$DEST"
  $VENV alt-largecap/flash.py --push --history-scan
} >> "$LOG" 2>&1
