#!/usr/bin/env bash
set -euo pipefail
cd /root/data-download
python3 - <<'PY'
from pathlib import Path
for rel in [
    "pm-fees/download.py",
    "pm-fees/flash.py",
    "run_misc_daily.sh",
]:
    p = Path("/root/data-download") / rel
    if p.is_file():
        t = p.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        p.write_bytes(t)
print("lf ok")
PY
.venv/bin/python pm-fees/download.py
mkdir -p /root/dashboard/public/json/pm-fees
cp -a pm-fees/output/json/pm_fees.json /root/dashboard/public/json/pm-fees/pm_fees.json
.venv/bin/python pm-fees/flash.py
curl -sI "https://data.wublock123.com/json/pm-fees/pm_fees.json" | head -5
echo DEPLOY_PM_FEES_OK
