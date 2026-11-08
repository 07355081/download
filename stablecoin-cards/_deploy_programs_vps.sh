#!/usr/bin/env bash
set -euo pipefail
KEY="${HOME}/.ssh/data-dashboard-prod"
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/stablecoin-cards
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=25 "$HOST" \
  'mkdir -p /root/data-download/stablecoin-cards/output/json /root/dashboard/public/json/crypto-card-programs'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SRC/download_programs.py" \
  "$SRC/flash_programs.py" \
  "$SRC/_patch_feed.js" \
  "$HOST:/root/data-download/stablecoin-cards/"

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/run_misc_daily.sh" \
  "$DD/copy_to_dashboard.py" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  "python3 -c \"from pathlib import Path
for p in [
 '/root/data-download/stablecoin-cards/download_programs.py',
 '/root/data-download/stablecoin-cards/flash_programs.py',
 '/root/data-download/stablecoin-cards/_patch_feed.js',
 '/root/data-download/run_misc_daily.sh',
 '/root/data-download/copy_to_dashboard.py',
]:
 t=Path(p).read_bytes().replace(b'\\r\\n', b'\\n').replace(b'\\r', b'\\n'); Path(p).write_bytes(t)\""

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python stablecoin-cards/download_programs.py && \
   mkdir -p /root/dashboard/public/json/crypto-card-programs && \
   cp -a stablecoin-cards/output/json/crypto_card_programs.json \
     /root/dashboard/public/json/crypto-card-programs/crypto_card_programs.json && \
   .venv/bin/python stablecoin-cards/flash_programs.py --push'

CID=$(ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" "docker ps --filter name=dashboard --format '{{.ID}}' | head -1")
if [[ -n "${CID:-}" ]]; then
  scp -i "$KEY" -o IdentitiesOnly=yes \
    "$SRC/_patch_feed.js" \
    "$HOST:/tmp/_patch_crypto_card_programs.js"
  ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
    "docker cp /tmp/_patch_crypto_card_programs.js ${CID}:/tmp/_patch_crypto_card_programs.js && \
     docker exec ${CID} node /tmp/_patch_crypto_card_programs.js && \
     docker restart ${CID}"
fi

echo DEPLOY_CRYPTO_CARD_PROGRAMS_OK
