#!/bin/bash
set -e
python3 - <<'PY'
import json
from pathlib import Path
d=json.loads(Path("/root/dashboard/public/json/listed-cex-spot-share/listed_cex_spot_share.json").read_text())
print("as_of", d.get("as_of"))
print("shares", d["latest"]["shares"])
print("gap", d["latest"]["gap_pp"], "leader", d["latest"]["leader"], "n", len(d["data"]))
PY
docker exec dashboard sh -c 'grep -o "listed-cex-spot-share" /app/.next/server/app/dashboard/lark-feeds/page.js | head -3'
cd /root/data-download
/root/data-download/.venv/bin/python listed-cex-spot-share/flash.py --push --force
