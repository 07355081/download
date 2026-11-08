#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
GM=/mnt/e/Code/data-download/gmgn
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=15 "$HOST" \
  'mkdir -p /root/data-download/gmgn/output/json'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$GM/download.py" "$GM/flash.py" "$GM/_probe_arc.py" \
  "$HOST:/root/data-download/gmgn/"

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/realtime_ledger.py" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'sed -i "s/\r$//" /root/data-download/gmgn/download.py /root/data-download/gmgn/flash.py /root/data-download/gmgn/_probe_arc.py /root/data-download/realtime_ledger.py'

echo "== probe arc =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python gmgn/_probe_arc.py'

echo "== download all chains =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python gmgn/download.py'

echo "== copy dashboard =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python copy_to_dashboard.py --dest /root/dashboard/public/json --only gmgn'

echo "== final snapshot =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python gmgn/_probe_arc.py'

echo DEPLOY_GMGN_ARC_OK
