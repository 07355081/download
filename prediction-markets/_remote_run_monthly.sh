#!/usr/bin/env bash
set -euo pipefail
cd /root/data-download
python3 - <<'PY'
from pathlib import Path
for p in [
    "/root/data-download/prediction-markets/monthly_flash.py",
    "/root/data-download/run_misc_daily.sh",
]:
    t = Path(p).read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    Path(p).write_bytes(t)
print("lf ok")
PY
.venv/bin/python prediction-markets/download.py
cp -a prediction-markets/output/json/prediction_markets_quarterly.json \
  /root/dashboard/public/json/prediction_markets_quarterly.json
echo "== dry =="
.venv/bin/python prediction-markets/monthly_flash.py
echo "== push =="
set -a
[ -f .env ] && . ./.env
[ -f /root/dashboard/.env.live ] && . /root/dashboard/.env.live
set +a
.venv/bin/python prediction-markets/monthly_flash.py --push
grep -n monthly_flash run_misc_daily.sh | head -5
echo DEPLOY_PM_MONTHLY_OK
