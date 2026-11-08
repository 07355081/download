#!/usr/bin/env bash
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
scp -i "$KEY" -o IdentitiesOnly=yes /mnt/e/Code/data-download/_probe_perp_free2.py "$HOST:/tmp/"
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  '/root/data-download/.venv/bin/python /tmp/_probe_perp_free2.py' \
  > /mnt/e/Code/data-download/_perp_probe2.txt 2>&1 || true
wc -l /mnt/e/Code/data-download/_perp_probe2.txt
