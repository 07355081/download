#!/usr/bin/env bash
set -euo pipefail
KEY="${KEY:-$HOME/.ssh/data-dashboard-prod}"
HOST=root@47.74.6.207
scp -i "$KEY" -o IdentitiesOnly=yes \
  /mnt/e/Code/data-download/solana-quarter/_patch_titles.js \
  /mnt/e/Code/data-download/solana-quarter/_add_series.py \
  "$HOST:/tmp/"
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" 'sed -i "s/\r$//" /tmp/_add_series.py && python3 /tmp/_add_series.py && docker cp /tmp/_patch_titles.js dashboard:/tmp/_patch_titles.js && docker exec dashboard node /tmp/_patch_titles.js'
