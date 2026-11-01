#!/bin/bash
# 每小时看一眼基金会关联钱包的大额转入。建议 crontab:
#   25 * * * * /root/data-download/ef-wallets/run_hourly.sh
# 首次先 bootstrap，避免把已经公开的历史转账再推一遍。
set -o pipefail
cd /root/data-download || exit 1
VENV=./.venv/bin/python
LOG=/root/data-download/ef-wallets/_hourly.log
DEST=/root/dashboard/public/json
{
  echo "======== [$(date '+%Y-%m-%d %H:%M:%S')] ef-wallets ========"
  $VENV ef-wallets/watch.py --push
  $VENV copy_to_dashboard.py --only ef-wallets --dest "$DEST"
} >> "$LOG" 2>&1
