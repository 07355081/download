#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
scp -i "$KEY" -o IdentitiesOnly=yes \
  /mnt/e/Code/data-download/_probe_cg_tokenized.py \
  /mnt/e/Code/data-download/_inspect_rwa_aum.py \
  "$HOST:/tmp/"
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  '/root/data-download/.venv/bin/python /tmp/_probe_cg_tokenized.py' \
  > /mnt/e/Code/data-download/_cg_out.txt 2>&1 || true
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  '/root/data-download/.venv/bin/python /tmp/_inspect_rwa_aum.py' \
  > /mnt/e/Code/data-download/_rwa_aum_meta.txt 2>&1 || true
echo DONE
wc -l /mnt/e/Code/data-download/_cg_out.txt /mnt/e/Code/data-download/_rwa_aum_meta.txt
