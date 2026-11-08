#!/usr/bin/env bash
set -euo pipefail
KEY="${HOME}/.ssh/data-dashboard-prod"
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/tokenized-treasury-products
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=25 "$HOST" \
  'mkdir -p /root/data-download/tokenized-treasury-products/output/json /root/dashboard/public/json/tokenized-treasury-products'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SRC/download.py" \
  "$SRC/flash.py" \
  "$SRC/_patch_feed.js" \
  "$HOST:/root/data-download/tokenized-treasury-products/"

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  "python3 -c \"from pathlib import Path
for p in [
 '/root/data-download/tokenized-treasury-products/download.py',
 '/root/data-download/tokenized-treasury-products/flash.py',
 '/root/data-download/tokenized-treasury-products/_patch_feed.js',
 '/root/data-download/run_misc_daily.sh',
]:
 t=Path(p).read_bytes().replace(b'\\r\\n', b'\\n').replace(b'\\r', b'\\n'); Path(p).write_bytes(t)\""

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python tokenized-treasury-products/download.py && \
   mkdir -p /root/dashboard/public/json/tokenized-treasury-products && \
   cp -a tokenized-treasury-products/output/json/tokenized_treasury_products.json \
     /root/dashboard/public/json/tokenized-treasury-products/tokenized_treasury_products.json && \
   .venv/bin/python tokenized-treasury-products/flash.py --push'

CID=$(ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" "docker ps --filter name=dashboard --format '{{.ID}}' | head -1")
if [[ -n "${CID:-}" ]]; then
  scp -i "$KEY" -o IdentitiesOnly=yes "$SRC/_patch_feed.js" "$HOST:/tmp/_patch_tt_products.js"
  ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
    "docker cp /tmp/_patch_tt_products.js ${CID}:/tmp/_patch_tt_products.js && \
     docker exec ${CID} node /tmp/_patch_tt_products.js && \
     docker restart ${CID}"
fi

echo DEPLOY_TOKENIZED_TREASURY_PRODUCTS_OK
