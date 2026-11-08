#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/aave-v4-spokes
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=15 "$HOST" \
  'mkdir -p /root/data-download/aave-v4-spokes/output/json /root/dashboard/public/json/aave-v4-spokes'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SRC/download.py" "$SRC/flash.py" \
  "$HOST:/root/data-download/aave-v4-spokes/"

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'sed -i "s/\r$//" /root/data-download/aave-v4-spokes/download.py /root/data-download/aave-v4-spokes/flash.py /root/data-download/run_misc_daily.sh && chmod +x /root/data-download/run_misc_daily.sh'

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python aave-v4-spokes/download.py && cp -a aave-v4-spokes/output/json/aave_v4_spokes.json /root/dashboard/public/json/aave-v4-spokes/aave_v4_spokes.json && .venv/bin/python aave-v4-spokes/flash.py --push && grep -n aave-v4-spokes run_misc_daily.sh | head'

echo DEPLOY_AAVE_V4_SPOKES_OK
