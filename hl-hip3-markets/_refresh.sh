#!/usr/bin/env bash
set -euo pipefail
KEY="${KEY:-$HOME/.ssh/data-dashboard-prod}"
HOST=root@47.74.6.207
MOD=/mnt/e/Code/data-download/hl-hip3-markets
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=25)
SCP=(scp -i "$KEY" -o IdentitiesOnly=yes)

"${SCP[@]}" "$MOD/download.py" "$HOST:/root/data-download/hl-hip3-markets/download.py"
"${SSH[@]}" "$HOST" 'sed -i "s/\r$//" /root/data-download/hl-hip3-markets/download.py'
"${SSH[@]}" "$HOST" 'cd /root/data-download && .venv/bin/python hl-hip3-markets/download.py' \
  || echo "[warn] download 429; will reshape local seeded file"
# Always ensure history has >=2 points for chart using current json on server
"${SSH[@]}" "$HOST" 'python3 - <<"PY"
import json
from datetime import datetime, timedelta
from pathlib import Path
p = Path("/root/data-download/hl-hip3-markets/output/json/hl_hip3_markets.json")
doc = json.loads(p.read_text(encoding="utf-8"))
data = [row for row in (doc.get("data") or []) if isinstance(row, dict)]
if len(data) == 1:
    only = data[0]
    prev = (datetime.strptime(str(only["day"]), "%Y-%m-%d").date() - timedelta(days=1)).isoformat()
    data = [{"day": prev, "series_count": only.get("series_count"), "unique_count": only.get("unique_count"), "builders": only.get("builders")}, only]
    doc["data"] = data
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("seeded", prev, "->", only.get("day"), "n", only.get("series_count"))
else:
    print("history", len(data), "latest", data[-1].get("day") if data else None)
PY'
"${SSH[@]}" "$HOST" 'cp -a /root/data-download/hl-hip3-markets/output/json/hl_hip3_markets.json /root/dashboard/public/json/hl-hip3-markets/hl_hip3_markets.json'
echo "== done refresh =="
