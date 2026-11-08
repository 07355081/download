#!/usr/bin/env bash
set -euo pipefail
KEY="${KEY:-$HOME/.ssh/data-dashboard-prod}"
scp -i "$KEY" -o IdentitiesOnly=yes \
  /mnt/e/Code/data-download/hl-net-revenue/flash.py \
  root@47.74.6.207:/root/data-download/hl-net-revenue/flash.py
ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=25 root@47.74.6.207 \
  "sed -i 's/\r$//' /root/data-download/hl-net-revenue/flash.py"
