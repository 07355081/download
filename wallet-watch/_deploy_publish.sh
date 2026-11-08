#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/wallet-watch

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SRC/publish_latest.py" "$SRC/run_hourly.sh" \
  "$HOST:/root/data-download/wallet-watch/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'sed -i "s/\r$//" /root/data-download/wallet-watch/publish_latest.py /root/data-download/wallet-watch/run_hourly.sh && cd /root/data-download && .venv/bin/python wallet-watch/publish_latest.py && ls -la /root/dashboard/public/json/wallet-watch/latest.json && python3 -c "import json;d=json.load(open(\"/root/dashboard/public/json/wallet-watch/latest.json\"));print(d[\"count\"], d.get(\"day\"), len(d.get(\"days\") or []), len(d.get(\"top\") or []))"'

echo WALLET_WATCH_PUBLISH_OK
