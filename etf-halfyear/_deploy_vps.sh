#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/etf-halfyear
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=15 "$HOST" \
  'mkdir -p /root/data-download/etf-halfyear/output/json /root/dashboard/public/json/etf-halfyear'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SRC/download.py" "$SRC/flash.py" \
  "$HOST:/root/data-download/etf-halfyear/"

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'sed -i "s/\r$//" /root/data-download/etf-halfyear/download.py /root/data-download/etf-halfyear/flash.py /root/data-download/run_misc_daily.sh && chmod +x /root/data-download/run_misc_daily.sh'

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python etf-halfyear/download.py && cp -a etf-halfyear/output/json/etf_halfyear.json /root/dashboard/public/json/etf-halfyear/etf_halfyear.json && .venv/bin/python etf-halfyear/flash.py --push && .venv/bin/python etf-halfyear/flash.py && grep -n etf-halfyear run_misc_daily.sh | head'

echo DEPLOY_ETF_HALFYEAR_OK
