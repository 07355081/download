#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/robinhood-chain
DD=/mnt/e/Code/data-download

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SRC/stock_compare_download.py" "$SRC/stock_compare_flash.py" \
  "$HOST:/root/data-download/robinhood-chain/"
scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  "python3 -c \"from pathlib import Path
for p in ['/root/data-download/robinhood-chain/stock_compare_download.py','/root/data-download/robinhood-chain/stock_compare_flash.py','/root/data-download/run_misc_daily.sh']:
 t=Path(p).read_bytes().replace(b'\\r\\n', b'\\n').replace(b'\\r', b'\\n'); Path(p).write_bytes(t)\""

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python robinhood-chain/stock_compare_download.py && mkdir -p /root/dashboard/public/json/robinhood-chain && cp -a robinhood-chain/output/json/stock_token_compare.json /root/dashboard/public/json/robinhood-chain/stock_token_compare.json && .venv/bin/python robinhood-chain/stock_compare_flash.py --push'

echo DEPLOY_STOCK_COMPARE_OK
