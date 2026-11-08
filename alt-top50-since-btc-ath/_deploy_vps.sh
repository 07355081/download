#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
MOD=/mnt/e/Code/data-download/alt-top50-since-btc-ath
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=15 "$HOST" \
  'mkdir -p /root/data-download/alt-top50-since-btc-ath/output/json'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$MOD/download.py" "$MOD/flash.py" \
  "$HOST:/root/data-download/alt-top50-since-btc-ath/"

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/copy_to_dashboard.py" "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'sed -i "s/\r$//" \
    /root/data-download/alt-top50-since-btc-ath/download.py \
    /root/data-download/alt-top50-since-btc-ath/flash.py \
    /root/data-download/copy_to_dashboard.py \
    /root/data-download/run_misc_daily.sh \
    && chmod +x /root/data-download/run_misc_daily.sh'

echo "== download =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python alt-top50-since-btc-ath/download.py'

echo "== copy dashboard =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python copy_to_dashboard.py --dest /root/dashboard/public/json --only alt-top50-since-btc-ath'

echo "== flash dry-run =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python alt-top50-since-btc-ath/flash.py --history-scan'

echo "== flash push =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python alt-top50-since-btc-ath/flash.py --push --history-scan'

echo "== snapshot =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python -c "
import json
from pathlib import Path
p=Path(\"alt-top50-since-btc-ath/output/json/alt_top50_btc_ath_latest.json\")
d=json.loads(p.read_text())
print(\"day\", d.get(\"day\"), \"ath\", d.get(\"ath_day\"), \"window\", d.get(\"window_days\"))
print(\"beat\", d.get(\"n_beat_btc\"), \"/\", d.get(\"n_priced\"), \"med_rel\", d.get(\"median_rel\"), \"btc_pct\", d.get(\"btc_pct\"))
print(\"leaders\", [(x.get(\"symbol\"), x.get(\"rel\")) for x in (d.get(\"leaders\") or [])[:8]])
"'
