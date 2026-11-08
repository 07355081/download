#!/bin/bash
set -eu
cd /root/data-download
python3 - <<'PY'
from pathlib import Path
for name in ("download.py", "flash.py", "_patch_feed.js"):
    p = Path("/root/data-download/protocol-revenue-ex") / name
    if p.is_file():
        p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
misc = Path("/root/data-download/run_misc_daily.sh")
if misc.is_file():
    misc.write_bytes(misc.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
PY
VENV=/root/data-download/.venv/bin/python
[ -x "$VENV" ] || VENV=python3
$VENV protocol-revenue-ex/download.py
mkdir -p /root/dashboard/public/json/protocol-revenue-ex
cp -a protocol-revenue-ex/output/json/protocol_revenue_ex.json \
  /root/dashboard/public/json/protocol-revenue-ex/protocol_revenue_ex.json
docker cp protocol-revenue-ex/_patch_feed.js dashboard:/tmp/_patch_protocol_revenue_ex.js
docker exec dashboard node /tmp/_patch_protocol_revenue_ex.js
$VENV protocol-revenue-ex/flash.py --push
curl -s -o /dev/null -w "json:%{http_code}\n" https://data.wublock123.com/json/protocol-revenue-ex/protocol_revenue_ex.json
curl -s -o /dev/null -w "page:%{http_code}\n" https://data.wublock123.com/dashboard/lark-feeds/protocol-revenue-ex
