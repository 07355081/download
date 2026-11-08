#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" 'mkdir -p /root/data-download/lookonchain-week/output'
scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/lookonchain-week/flash.py" "$DD/lookonchain-week/README.md" \
  "$HOST:/root/data-download/lookonchain-week/"
scp -i "$KEY" -o IdentitiesOnly=yes "$DD/run_misc_daily.sh" "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" bash -s <<'REMOTE'
set -euo pipefail
sed -i 's/\r$//' /root/data-download/lookonchain-week/flash.py /root/data-download/run_misc_daily.sh
cd /root/data-download
echo '=== DRY ==='
.venv/bin/python lookonchain-week/flash.py
echo '=== PUSH ==='
.venv/bin/python lookonchain-week/flash.py --push --history-scan
REMOTE
echo DEPLOY_WEEK_FLASH_OK
