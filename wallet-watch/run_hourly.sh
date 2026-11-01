#!/bin/bash
# 地址监控小时任务(不占 Dune)。建议 crontab:
#   10 * * * * /root/data-download/wallet-watch/run_hourly.sh >> /root/data-download/wallet-watch/_hourly.log 2>&1
# 首次上线:
#   Ledger:  python monitor.py --bootstrap
#   USG:     python monitor.py --watchlist watchlist_usg_bitfinex.json --history-alert --push
set -o pipefail
cd /root/data-download || exit 1
VENV=./.venv/bin/python
LOG=/root/data-download/wallet-watch/_hourly.log
ts() { date '+%Y-%m-%d %H:%M:%S'; }

{
  echo "======== [$(ts)] wallet-watch ledger ========"
  $VENV wallet-watch/monitor.py --push

  echo "======== [$(ts)] wallet-watch usg_bitfinex ========"
  $VENV wallet-watch/monitor.py --watchlist wallet-watch/watchlist_usg_bitfinex.json --push

  echo "======== [$(ts)] wallet-watch pump_sol ========"
  $VENV wallet-watch/monitor_pump_sol.py --push

  echo "======== [$(ts)] wallet-watch yzi_ena ========"
  $VENV wallet-watch/monitor_yzi_ena.py --push

  echo "======== [$(ts)] wallet-watch publish latest.json ========"
  $VENV wallet-watch/publish_latest.py
} >> "$LOG" 2>&1
