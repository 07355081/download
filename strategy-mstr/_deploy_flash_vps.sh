#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
MOD=/mnt/e/Code/data-download/strategy-mstr
DD=/mnt/e/Code/data-download

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$MOD/flash.py" \
  "$HOST:/root/data-download/strategy-mstr/"

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/run_strategy_mstr_daily.sh" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'sed -i "s/\r$//" \
    /root/data-download/strategy-mstr/flash.py \
    /root/data-download/run_strategy_mstr_daily.sh \
    && chmod +x /root/data-download/run_strategy_mstr_daily.sh'

echo "== flash dry-run =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python strategy-mstr/flash.py --history-scan --no-daily'

echo "== snapshot days =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'ls /root/data-download/strategy-mstr/output/json/snapshots | tail -8'
