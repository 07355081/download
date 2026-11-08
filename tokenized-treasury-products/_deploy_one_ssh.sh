#!/usr/bin/env bash
set -euo pipefail
KEY="${HOME}/.ssh/data-dashboard-prod"
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/tokenized-treasury-products
DD=/mnt/e/Code/data-download

# one SSH session via ControlMaster
CTL=/tmp/dd-ssh-%r@%h:%p
ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=25 \
  -o ControlMaster=auto -o ControlPersist=120 -o ControlPath="$CTL" \
  "$HOST" 'mkdir -p /root/data-download/tokenized-treasury-products/output/json /root/dashboard/public/json/tokenized-treasury-products'

scp -i "$KEY" -o IdentitiesOnly=yes -o ControlPath="$CTL" \
  "$SRC/download.py" "$SRC/flash.py" "$SRC/_patch_feed.js" \
  "$HOST:/root/data-download/tokenized-treasury-products/"

scp -i "$KEY" -o IdentitiesOnly=yes -o ControlPath="$CTL" \
  "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/"

# also push local json so flash baseline can run even if CG flaky
if [[ -f "$SRC/output/json/tokenized_treasury_products.json" ]]; then
  scp -i "$KEY" -o IdentitiesOnly=yes -o ControlPath="$CTL" \
    "$SRC/output/json/tokenized_treasury_products.json" \
    "$HOST:/root/data-download/tokenized-treasury-products/output/json/"
fi

ssh -i "$KEY" -o IdentitiesOnly=yes -o ControlPath="$CTL" "$HOST" '
python3 - <<PY
from pathlib import Path
for p in [
 "/root/data-download/tokenized-treasury-products/download.py",
 "/root/data-download/tokenized-treasury-products/flash.py",
 "/root/data-download/tokenized-treasury-products/_patch_feed.js",
 "/root/data-download/run_misc_daily.sh",
]:
 t=Path(p).read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"); Path(p).write_bytes(t)
print("lf ok")
PY
cd /root/data-download
.venv/bin/python tokenized-treasury-products/download.py || echo DOWNLOAD_WARN
mkdir -p /root/dashboard/public/json/tokenized-treasury-products
cp -a tokenized-treasury-products/output/json/tokenized_treasury_products.json \
  /root/dashboard/public/json/tokenized-treasury-products/tokenized_treasury_products.json
.venv/bin/python tokenized-treasury-products/flash.py --push || echo FLASH_WARN
CID=$(docker ps --filter name=dashboard --format "{{.ID}}" | head -1)
if [ -n "$CID" ]; then
  docker cp /root/data-download/tokenized-treasury-products/_patch_feed.js "$CID":/tmp/_patch_tt_products.js
  docker exec "$CID" node /tmp/_patch_tt_products.js || true
  docker restart "$CID" || true
fi
echo DEPLOY_OK
'

ssh -i "$KEY" -o IdentitiesOnly=yes -o ControlPath="$CTL" -O exit "$HOST" 2>/dev/null || true
echo DONE
