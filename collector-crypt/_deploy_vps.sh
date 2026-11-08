#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/collector-crypt
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=20 "$HOST" \
  'mkdir -p /root/data-download/collector-crypt/output/json /root/dashboard/public/json/collector-crypt'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SRC/flash.py" \
  "$HOST:/root/data-download/collector-crypt/"
scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  "python3 -c \"from pathlib import Path
for p in ['/root/data-download/collector-crypt/flash.py','/root/data-download/run_misc_daily.sh']:
 t=Path(p).read_bytes().replace(b'\\r\\n', b'\\n').replace(b'\\r', b'\\n'); Path(p).write_bytes(t)\""

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python collector-crypt/flash.py && mkdir -p /root/dashboard/public/json/collector-crypt && cp -a collector-crypt/output/json/collector_crypt_monthly.json /root/dashboard/public/json/collector-crypt/collector_crypt_monthly.json 2>/dev/null || true'

echo DEPLOY_COLLECTOR_CRYPT_OK
