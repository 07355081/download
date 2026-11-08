#!/usr/bin/env bash
set -euo pipefail
KEY="${KEY:-$HOME/.ssh/data-dashboard-prod}"
HOST=root@47.74.6.207
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=25)
SCP=(scp -i "$KEY" -o IdentitiesOnly=yes)
MOD=/mnt/e/Code/data-download/hl-hip3-markets
"${SCP[@]}" "$MOD/download.py" "$HOST:/root/data-download/hl-hip3-markets/download.py"
"${SSH[@]}" "$HOST" "sed -i 's/\r$//' /root/data-download/hl-hip3-markets/download.py"
"${SSH[@]}" "$HOST" 'python3 - <<"PY"
import json
from pathlib import Path
p=Path("/root/data-download/hl-hip3-markets/output/json/hl_hip3_markets.json")
doc=json.loads(p.read_text(encoding="utf-8"))
doc.pop("day", None)
# keep 2-point history
data=[r for r in (doc.get("data") or []) if isinstance(r, dict)]
doc["data"]=data
p.write_text(json.dumps(doc, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
print("ok", "day" in doc, len(data), (doc.get("latest") or {}).get("series_count"))
PY'
"${SSH[@]}" "$HOST" 'cp -a /root/data-download/hl-hip3-markets/output/json/hl_hip3_markets.json /root/dashboard/public/json/hl-hip3-markets/hl_hip3_markets.json'
echo done
