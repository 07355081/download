#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
GM=/mnt/e/Code/data-download/gmgn
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=15 "$HOST" \
  'mkdir -p /root/data-download/gmgn/output/json'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$GM/download.py" "$GM/flash.py" \
  "$HOST:/root/data-download/gmgn/"

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/realtime_ledger.py" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'sed -i "s/\r$//" /root/data-download/gmgn/download.py /root/data-download/gmgn/flash.py /root/data-download/realtime_ledger.py'

echo "== download (incl eth) =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python gmgn/download.py'

echo "== copy dashboard =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python copy_to_dashboard.py --dest /root/dashboard/public/json --only gmgn'

echo "== snapshot =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python -c "
import json
from pathlib import Path
d=json.loads(Path(\"gmgn/output/json/meme_markets_24h.json\").read_text())
print(\"total\", d[\"total_volume\"], \"dod\", d.get(\"dod_pct\"))
for c in d[\"chains\"]:
    print(f\"  {c[\"label\"]}: {c[\"volume\"]:.0f} ({c[\"share\"]:.1f}%) dod={c.get(\"dod_pct\")}\")
print(\"top\", [t.get(\"symbol\") for t in (d.get(\"top_tokens\") or [])[:3]])
"'

echo DEPLOY_GMGN_ETH_OK
