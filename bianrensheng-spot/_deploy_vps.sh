#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/bianrensheng-spot
SBL=/mnt/e/Code/data-download/spot-break-liq

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=15 "$HOST" \
  'mkdir -p /root/data-download/bianrensheng-spot/output/json /root/dashboard/public/json/bianrensheng-spot'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SRC/monitor.py" \
  "$HOST:/root/data-download/bianrensheng-spot/"
scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SBL/run_monitor.sh" \
  "$HOST:/root/data-download/spot-break-liq/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  "python3 -c \"from pathlib import Path
for p in ['/root/data-download/bianrensheng-spot/monitor.py','/root/data-download/spot-break-liq/run_monitor.sh']:
 t=Path(p).read_bytes().replace(b'\\r\\n', b'\\n').replace(b'\\r', b'\\n'); Path(p).write_bytes(t)\""

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python bianrensheng-spot/monitor.py --push && mkdir -p /root/dashboard/public/json/bianrensheng-spot && cp -a bianrensheng-spot/output/json/bianrensheng_spot.json /root/dashboard/public/json/bianrensheng-spot/bianrensheng_spot.json'

echo DEPLOY_BIANRENSHENG_SPOT_OK
