#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/btc-hashrate-puell
DD=/mnt/e/Code/data-download

ssh -o ConnectTimeout=25 -o IdentitiesOnly=yes -i "$KEY" "$HOST" \
  'mkdir -p /root/data-download/btc-hashrate-puell/output/json /root/dashboard/public/json/btc-hashrate-puell'

scp -o ConnectTimeout=25 -o IdentitiesOnly=yes -i "$KEY" \
  "$SRC/download.py" "$SRC/flash.py" \
  "$HOST:/root/data-download/btc-hashrate-puell/"

scp -o ConnectTimeout=25 -o IdentitiesOnly=yes -i "$KEY" \
  "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/"

ssh -o ConnectTimeout=25 -o IdentitiesOnly=yes -i "$KEY" "$HOST" \
  'sed -i "s/\r$//" /root/data-download/btc-hashrate-puell/download.py /root/data-download/btc-hashrate-puell/flash.py /root/data-download/run_misc_daily.sh && chmod +x /root/data-download/run_misc_daily.sh'

ssh -o ConnectTimeout=25 -o IdentitiesOnly=yes -i "$KEY" "$HOST" \
  'cd /root/data-download && .venv/bin/python btc-hashrate-puell/download.py && cp -a btc-hashrate-puell/output/json/btc_hashrate_puell.json /root/dashboard/public/json/btc-hashrate-puell/btc_hashrate_puell.json && .venv/bin/python btc-hashrate-puell/flash.py --push && grep -n btc-hashrate-puell run_misc_daily.sh | head'

echo DEPLOY_BTC_HASHRATE_PUELL_OK
