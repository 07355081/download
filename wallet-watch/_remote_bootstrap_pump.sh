#!/bin/bash
set -euo pipefail
cd /root/data-download
sed -i 's/\r$//' wallet-watch/monitor_pump_sol.py wallet-watch/run_hourly.sh || true
pkill -f monitor_pump_sol.py 2>/dev/null || true
./.venv/bin/python wallet-watch/monitor_pump_sol.py --bootstrap
echo "BOOT_EXIT=$?"
ls -la wallet-watch/output/state_watchlist_pump_sol.json
wc -c wallet-watch/output/state_watchlist_pump_sol.json
crontab -l 2>/dev/null | grep wallet-watch || echo "NO_WALLET_WATCH_CRON"
