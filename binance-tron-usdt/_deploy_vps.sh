#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/binance-tron-usdt
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=15 "$HOST" \
  'mkdir -p /root/data-download/binance-tron-usdt/output/json /root/dashboard/public/json/binance-tron-usdt'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SRC/download.py" "$SRC/flash.py" \
  "$HOST:/root/data-download/binance-tron-usdt/"
scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  "python3 -c \"from pathlib import Path
for p in ['/root/data-download/binance-tron-usdt/download.py','/root/data-download/binance-tron-usdt/flash.py','/root/data-download/run_misc_daily.sh']:
 t=Path(p).read_bytes().replace(b'\\r\\n', b'\\n').replace(b'\\r', b'\\n'); Path(p).write_bytes(t)\""

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python binance-tron-usdt/download.py && cp -a binance-tron-usdt/output/json/binance_tron_usdt.json /root/dashboard/public/json/binance-tron-usdt/binance_tron_usdt.json && .venv/bin/python binance-tron-usdt/flash.py --push'

echo DEPLOY_BINANCE_TRON_USDT_OK
