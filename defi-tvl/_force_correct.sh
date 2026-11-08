#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=20 "$HOST" \
  'cd /root/data-download && .venv/bin/python defi-tvl/flash.py --push --force'
echo FORCE_OK
