#!/usr/bin/env bash
# 把 RWA Lark 脚本和每日管道挂到线上。不强制重跑 Dune。
set -euo pipefail
KEY="${KEY:-$HOME/.ssh/data-dashboard-prod}"
HOST=root@47.74.6.207
RH=/mnt/e/Code/data-download/robinhood-chain
DD=/mnt/e/Code/data-download
if [ ! -f "$RH/rwa_flash.py" ]; then
  RH="$(cd "$(dirname "$0")" && pwd)"
  DD="$(cd "$RH/.." && pwd)"
fi

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" 'mkdir -p /root/data-download/robinhood-chain/output/json'
scp -i "$KEY" -o IdentitiesOnly=yes \
  "$RH/rwa_flash.py" \
  "$HOST:/root/data-download/robinhood-chain/"
scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" 'sed -i "s/\r$//" /root/data-download/run_misc_daily.sh /root/data-download/robinhood-chain/rwa_flash.py && cd /root/data-download && .venv/bin/python robinhood-chain/rwa_flash.py --history-scan && .venv/bin/python robinhood-chain/rwa_flash.py --push --history-scan'

echo DEPLOY_RWA_FLASH_VPS_OK
