#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
RH=/mnt/e/Code/data-download/robinhood-chain
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" 'mkdir -p /root/data-download/robinhood-chain/output/json'
scp -i "$KEY" -o IdentitiesOnly=yes \
  "$RH/download_fees.py" "$RH/fees_flash.py" "$RH/run_all.py" \
  "$HOST:/root/data-download/robinhood-chain/"
scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/run_misc_daily.sh" "$DD/copy_to_dashboard.py" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" 'sed -i "s/\r$//" /root/data-download/run_misc_daily.sh /root/data-download/robinhood-chain/download_fees.py /root/data-download/robinhood-chain/fees_flash.py /root/data-download/robinhood-chain/run_all.py /root/data-download/copy_to_dashboard.py'

# On VPS: download fees without pruning other robinhood JSON.
# copy_to_dashboard --only robinhood-chain mirrors from output/json which may only
# contain rh_fees_daily locally on first run — so copy the one file manually, then
# optionally merge via python.
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" 'cd /root/data-download && .venv/bin/python robinhood-chain/download_fees.py && cp -a robinhood-chain/output/json/rh_fees_daily.json /root/dashboard/public/json/robinhood-chain/rh_fees_daily.json && ls -la /root/dashboard/public/json/robinhood-chain/ && .venv/bin/python robinhood-chain/fees_flash.py --push --history-scan'

echo DEPLOY_FEES_VPS_OK
