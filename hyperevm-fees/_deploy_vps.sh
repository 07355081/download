#!/usr/bin/env bash
set -euo pipefail
cd /root/data-download
python3 - <<'PY'
from pathlib import Path
for rel in [
    "hyperevm-fees/download.py",
    "hyperevm-fees/flash.py",
    "run_misc_daily.sh",
]:
    p = Path("/root/data-download") / rel
    if p.is_file():
        t = p.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        p.write_bytes(t)
print("lf ok")
PY
.venv/bin/python hyperevm-fees/download.py
mkdir -p /root/dashboard/public/json/hyperevm-fees
cp -a hyperevm-fees/output/json/hyperevm_fees.json /root/dashboard/public/json/hyperevm-fees/hyperevm_fees.json
.venv/bin/python hyperevm-fees/flash.py
curl -sI "https://data.wublock123.com/json/hyperevm-fees/hyperevm_fees.json" | head -5
echo DEPLOY_HYPEREVM_FEES_OK
