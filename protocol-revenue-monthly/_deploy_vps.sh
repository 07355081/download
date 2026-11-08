#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'mkdir -p /root/data-download/protocol-revenue-monthly/output/json /root/data-download/protocol-revenue-monthly/cache'
scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/protocol-revenue-monthly/download.py" \
  "$DD/protocol-revenue-monthly/flash.py" \
  "$DD/protocol-revenue-monthly/README.md" \
  "$HOST:/root/data-download/protocol-revenue-monthly/"
scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/copy_to_dashboard.py" "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" bash -s <<'REMOTE'
set -euo pipefail
sed -i 's/\r$//' /root/data-download/protocol-revenue-monthly/*.py \
  /root/data-download/copy_to_dashboard.py /root/data-download/run_misc_daily.sh
cd /root/data-download
.venv/bin/python protocol-revenue-monthly/download.py
.venv/bin/python copy_to_dashboard.py --dest /root/dashboard/public/json --only protocol-revenue-monthly
echo '=== FLASH ==='
.venv/bin/python protocol-revenue-monthly/flash.py --push --history-scan
REMOTE
echo DEPLOY_PROTOCOL_REVENUE_MONTHLY_OK
