#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
OUT=/mnt/e/Code/data-download/_probe_rwa_vps_out.txt

scp -i "$KEY" -o IdentitiesOnly=yes \
  /mnt/e/Code/data-download/_probe_rwa_vps_remote.py \
  "$HOST:/tmp/_probe_rwa_vps_remote.py"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  '/root/data-download/.venv/bin/python /tmp/_probe_rwa_vps_remote.py' \
  >"$OUT" 2>&1 || true

# also check env for llama pro key (name only, no value)
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'grep -E "^(LLAMA|DEFILLAMA|TOKEN_TERMINAL|RWA_)" /root/dashboard/.env.live /root/data-download/.env 2>/dev/null | sed "s/=.*/=***/" || true' \
  >>"$OUT" 2>&1 || true

echo wrote "$OUT"
wc -l "$OUT"
