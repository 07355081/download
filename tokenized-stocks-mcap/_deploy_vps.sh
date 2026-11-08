#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/tokenized-stocks-mcap
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=15 "$HOST" \
  'mkdir -p /root/data-download/tokenized-stocks-mcap/output/json /root/dashboard/public/json/tokenized-stocks-mcap'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SRC/download.py" "$SRC/flash.py" "$SRC/chart.html" \
  "$HOST:/root/data-download/tokenized-stocks-mcap/"

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'sed -i "s/\r$//" /root/data-download/tokenized-stocks-mcap/download.py /root/data-download/tokenized-stocks-mcap/flash.py /root/data-download/run_misc_daily.sh && chmod +x /root/data-download/run_misc_daily.sh'

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python tokenized-stocks-mcap/download.py && cp -a tokenized-stocks-mcap/output/json/tokenized_stocks_mcap.json /root/dashboard/public/json/tokenized-stocks-mcap/tokenized_stocks_mcap.json && cp -a tokenized-stocks-mcap/chart.html /root/dashboard/public/json/tokenized-stocks-mcap/chart.html && .venv/bin/python tokenized-stocks-mcap/flash.py --push && grep -n tokenized-stocks-mcap run_misc_daily.sh | head'

echo DEPLOY_TOKENIZED_STOCKS_MCAP_OK
