#!/bin/bash
set -e
cd /root/data-download
python3 listed-cex-spot-share/_fix_nl.py
python3 - <<'PY'
from pathlib import Path
import shutil
p = Path("/tmp/run_misc_daily.sh")
if p.is_file():
    p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
    shutil.copy(p, "/root/data-download/run_misc_daily.sh")
    print("misc ok")
PY
VENV=/root/data-download/.venv/bin/python
[ -x "$VENV" ] || VENV=python3
$VENV listed-cex-spot-share/download.py
mkdir -p /root/dashboard/public/json/listed-cex-spot-share
cp -a listed-cex-spot-share/output/json/listed_cex_spot_share.json \
  /root/dashboard/public/json/listed-cex-spot-share/listed_cex_spot_share.json
docker exec dashboard mkdir -p /app/public/json/listed-cex-spot-share
docker cp /root/dashboard/public/json/listed-cex-spot-share/listed_cex_spot_share.json \
  dashboard:/app/public/json/listed-cex-spot-share/listed_cex_spot_share.json
docker cp listed-cex-spot-share/_patch_feed.js dashboard:/tmp/_patch_listed_cex.js
docker exec dashboard node /tmp/_patch_listed_cex.js
$VENV listed-cex-spot-share/flash.py --push
curl -s -o /dev/null -w "page:%{http_code}\n" http://127.0.0.1:3000/dashboard/lark-feeds/listed-cex-spot-share
curl -s -o /dev/null -w "json:%{http_code}\n" http://127.0.0.1:3000/json/listed-cex-spot-share/listed_cex_spot_share.json
$VENV - <<'PY'
import json
d=json.load(open("listed-cex-spot-share/output/json/listed_cex_spot_share.json"))
l=d["latest"]
print(l["time"], l["leader"], l["gap_pp"], l["shares"])
PY
