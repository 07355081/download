#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/spot-break-liq

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=15 "$HOST" \
  'mkdir -p /root/data-download/spot-break-liq'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SRC/monitor.py" \
  "$HOST:/root/data-download/spot-break-liq/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'sed -i "s/\r$//" /root/data-download/spot-break-liq/monitor.py'

echo "== dry-run =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download/spot-break-liq && /root/data-download/.venv/bin/python monitor.py 2>&1 | head -n 40'

echo DEPLOY_BREAKUP_OK
