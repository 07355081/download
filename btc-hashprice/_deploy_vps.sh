#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/btc-hashprice
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=15 "$HOST" \
  'mkdir -p /root/data-download/btc-hashprice/output/json /root/dashboard/public/json/btc-hashprice'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SRC/download.py" "$SRC/flash.py" \
  "$HOST:/root/data-download/btc-hashprice/"

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'sed -i "s/\r$//" /root/data-download/btc-hashprice/download.py /root/data-download/btc-hashprice/flash.py /root/data-download/run_misc_daily.sh && chmod +x /root/data-download/run_misc_daily.sh'

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python btc-hashprice/download.py && cp -a btc-hashprice/output/json/btc_hashprice.json /root/dashboard/public/json/btc-hashprice/btc_hashprice.json && .venv/bin/python btc-hashprice/flash.py --push && grep -n btc-hashprice run_misc_daily.sh | head'

echo DEPLOY_BTC_HASHPRICE_OK
