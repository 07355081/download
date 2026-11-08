#!/usr/bin/env bash
set -euo pipefail
KEY="${HOME}/.ssh/data-dashboard-prod"
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/non-usd-stables
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=25 "$HOST" \
  'mkdir -p /root/data-download/non-usd-stables/output/json /root/dashboard/public/json/non-usd-stables'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SRC/download.py" \
  "$SRC/flash.py" \
  "$SRC/_patch_feed.js" \
  "$HOST:/root/data-download/non-usd-stables/"

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/run_misc_daily.sh" \
  "$DD/copy_to_dashboard.py" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  "python3 -c \"from pathlib import Path
for p in [
 '/root/data-download/non-usd-stables/download.py',
 '/root/data-download/non-usd-stables/flash.py',
 '/root/data-download/non-usd-stables/_patch_feed.js',
 '/root/data-download/run_misc_daily.sh',
 '/root/data-download/copy_to_dashboard.py',
]:
 t=Path(p).read_bytes().replace(b'\\r\\n', b'\\n').replace(b'\\r', b'\\n'); Path(p).write_bytes(t)\""

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python non-usd-stables/download.py && \
   mkdir -p /root/dashboard/public/json/non-usd-stables && \
   cp -a non-usd-stables/output/json/non_usd_stables.json \
     /root/dashboard/public/json/non-usd-stables/non_usd_stables.json && \
   .venv/bin/python non-usd-stables/flash.py --push'

CID=$(ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" "docker ps --filter name=dashboard --format '{{.ID}}' | head -1")
if [[ -n "${CID:-}" ]]; then
  scp -i "$KEY" -o IdentitiesOnly=yes "$SRC/_patch_feed.js" "$HOST:/tmp/_patch_non_usd_stables.js"
  ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
    "docker cp /tmp/_patch_non_usd_stables.js ${CID}:/tmp/_patch_non_usd_stables.js && \
     docker exec ${CID} node /tmp/_patch_non_usd_stables.js && \
     docker restart ${CID}"
fi

echo DEPLOY_NON_USD_STABLES_OK
