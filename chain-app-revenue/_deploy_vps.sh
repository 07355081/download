#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
MOD=/mnt/e/Code/data-download/chain-app-revenue
DD=/mnt/e/Code/data-download
ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=15 "$HOST" \
  'mkdir -p /root/data-download/chain-app-revenue/output/json'
scp -i "$KEY" -o IdentitiesOnly=yes \
  "$MOD/download.py" "$MOD/flash.py" \
  "$HOST:/root/data-download/chain-app-revenue/"
scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/copy_to_dashboard.py" "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/"
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'sed -i "s/\r$//" \
    /root/data-download/chain-app-revenue/download.py \
    /root/data-download/chain-app-revenue/flash.py \
    /root/data-download/copy_to_dashboard.py \
    /root/data-download/run_misc_daily.sh'
echo "== download =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python chain-app-revenue/download.py'
echo "== copy =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python copy_to_dashboard.py --dest /root/dashboard/public/json --only chain-app-revenue'
echo "== flash push =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python chain-app-revenue/flash.py --push --history-scan'
