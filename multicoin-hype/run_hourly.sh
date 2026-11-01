#!/bin/bash
# 每小时看一眼已标注地址的大额 HYPE 转出。建议 crontab:
#   40 * * * * /root/data-download/multicoin-hype/run_hourly.sh
# 首次先 bootstrap，避免把已经公开的历史转账再推一遍。
set -o pipefail
cd /root/data-download || exit 1
VENV=./.venv/bin/python
LOG=/root/data-download/multicoin-hype/_hourly.log
DEST=/root/dashboard/public/json
{
  echo "======== [$(date '+%Y-%m-%d %H:%M:%S')] multicoin-hype ========"
  $VENV multicoin-hype/watch.py --push
  $VENV copy_to_dashboard.py --only multicoin-hype --dest "$DEST"
} >> "$LOG" 2>&1
