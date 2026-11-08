#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/hyperliquid
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=20 "$HOST" \
  'mkdir -p /root/data-download/hyperliquid/output/json /root/dashboard/public/json/hyperliquid'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SRC/tradexyz_oi_flash.py" \
  "$HOST:/root/data-download/hyperliquid/"
scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  "python3 -c \"from pathlib import Path
for p in ['/root/data-download/hyperliquid/tradexyz_oi_flash.py','/root/data-download/run_misc_daily.sh']:
 t=Path(p).read_bytes().replace(b'\\r\\n', b'\\n').replace(b'\\r', b'\\n'); Path(p).write_bytes(t)\""

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python hyperliquid/tradexyz_oi_flash.py && cp -a hyperliquid/output/json/tradexyz_oi.json /root/dashboard/public/json/hyperliquid/tradexyz_oi.json 2>/dev/null || true'

echo DEPLOY_TRADEXYZ_OI_OK
