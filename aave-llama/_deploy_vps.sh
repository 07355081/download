#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/aave-llama
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=15 "$HOST" \
  'mkdir -p /root/data-download/aave-llama/output/json /root/dashboard/public/json/aave-llama'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SRC/download.py" "$SRC/flash.py" \
  "$HOST:/root/data-download/aave-llama/"

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'sed -i "s/\r$//" /root/data-download/aave-llama/download.py /root/data-download/aave-llama/flash.py /root/data-download/run_misc_daily.sh'

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python aave-llama/download.py && cp -a aave-llama/output/json/aave_llama.json /root/dashboard/public/json/aave-llama/aave_llama.json && .venv/bin/python aave-llama/flash.py --push'

echo DEPLOY_AAVE_LLAMA_OK
