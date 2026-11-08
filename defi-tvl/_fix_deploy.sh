#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/defi-tvl
DD=/mnt/e/Code/data-download

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SRC/download.py" "$SRC/flash.py" \
  "$HOST:/root/data-download/defi-tvl/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'sed -i "s/\r$//" /root/data-download/defi-tvl/download.py /root/data-download/defi-tvl/flash.py'

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python defi-tvl/download.py && mkdir -p /root/dashboard/public/json/defi-tvl && cp -a defi-tvl/output/json/defi_tvl.json /root/dashboard/public/json/defi-tvl/defi_tvl.json && python3 -c "import json;d=json.load(open(\"/root/dashboard/public/json/defi-tvl/defi_tvl.json\"));r=d[\"data\"][-1];print(\"tip\",r);assert \"global\" in r and r[\"global\"]>0"'

echo DEFI_TVL_FIX_OK
