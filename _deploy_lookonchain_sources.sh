#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'mkdir -p /root/data-download/protocol-revenue-daily/output/json /root/data-download/perp-volume/output/json'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/protocol-revenue-daily/download.py" "$DD/protocol-revenue-daily/README.md" \
  "$HOST:/root/data-download/protocol-revenue-daily/"
scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/perp-volume/download.py" "$DD/perp-volume/README.md" \
  "$HOST:/root/data-download/perp-volume/"
scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/copy_to_dashboard.py" "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" bash -s <<'REMOTE'
set -euo pipefail
sed -i 's/\r$//' /root/data-download/protocol-revenue-daily/*.py /root/data-download/perp-volume/*.py \
  /root/data-download/copy_to_dashboard.py /root/data-download/run_misc_daily.sh
cd /root/data-download
.venv/bin/python protocol-revenue-daily/download.py
.venv/bin/python perp-volume/download.py
.venv/bin/python copy_to_dashboard.py --dest /root/dashboard/public/json --only protocol-revenue-daily
.venv/bin/python copy_to_dashboard.py --dest /root/dashboard/public/json --only perp-volume
# quick WoW peek for Lookonchain week 2026-09-20 (Sun)
.venv/bin/python - <<'PY'
import json
from pathlib import Path
rev=json.loads(Path('protocol-revenue-daily/output/json/protocol_revenue_daily.json').read_text())
perp=json.loads(Path('perp-volume/output/json/perp_volume.json').read_text())
print('REV latest', rev.get('latest_day'), rev.get('latest_revenue_usd'))
for w in rev.get('weeks_revenue') or []:
    if w.get('week_end') in ('2026-09-20','2026-09-13','2026-09-27'):
        print('REV week', w)
print('PERP latest', perp.get('latest_day'), perp.get('latest_global'))
for w in perp.get('weeks') or []:
    if w.get('week_end') in ('2026-09-20','2026-09-13','2026-09-27'):
        print('PERP week', w)
PY
REMOTE
echo DEPLOY_LOOKONCHAIN_SOURCES_OK
