#!/usr/bin/env bash
set -euo pipefail
KEY="${KEY:-$HOME/.ssh/data-dashboard-prod}"
HOST=root@47.74.6.207
scp -i "$KEY" -o IdentitiesOnly=yes \
  /mnt/e/Code/data-download/hl-net-revenue/_inspect_manifest.js \
  /mnt/e/Code/data-download/hl-net-revenue/_run_inspect.sh \
  "$HOST:/tmp/"
ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=25 "$HOST" \
  "sed -i 's/\r$//' /tmp/_run_inspect.sh && bash /tmp/_run_inspect.sh"
