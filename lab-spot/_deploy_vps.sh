#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/lab-spot
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=15 "$HOST" \
  'mkdir -p /root/data-download/lab-spot/output/json /root/dashboard/public/json/lab-spot'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SRC/download.py" "$SRC/flash.py" \
  "$HOST:/root/data-download/lab-spot/"
scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  "python3 -c \"from pathlib import Path
for p in ['/root/data-download/lab-spot/download.py','/root/data-download/lab-spot/flash.py','/root/data-download/run_misc_daily.sh']:
 t=Path(p).read_bytes().replace(b'\\r\\n', b'\\n').replace(b'\\r', b'\\n'); Path(p).write_bytes(t)\""

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python lab-spot/download.py && cp -a lab-spot/output/json/lab_spot.json /root/dashboard/public/json/lab-spot/lab_spot.json && .venv/bin/python lab-spot/flash.py --push'

echo DEPLOY_LAB_SPOT_OK
