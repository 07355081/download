#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/rlusd-chain
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=15 "$HOST" \
  'mkdir -p /root/data-download/rlusd-chain/output/json /root/dashboard/public/json/rlusd-chain'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SRC/download.py" "$SRC/flash.py" \
  "$HOST:/root/data-download/rlusd-chain/"
scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  "python3 -c \"from pathlib import Path
for p in ['/root/data-download/rlusd-chain/download.py','/root/data-download/rlusd-chain/flash.py','/root/data-download/run_misc_daily.sh']:
 t=Path(p).read_bytes().replace(b'\\r\\n', b'\\n').replace(b'\\r', b'\\n'); Path(p).write_bytes(t)\""

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python rlusd-chain/download.py && cp -a rlusd-chain/output/json/rlusd_chain.json /root/dashboard/public/json/rlusd-chain/rlusd_chain.json && .venv/bin/python rlusd-chain/flash.py --push'

echo DEPLOY_RLUSD_CHAIN_OK
