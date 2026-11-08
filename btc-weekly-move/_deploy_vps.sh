#!/bin/bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download
DST=/root/data-download
sed -i 's/\r$//' "$SRC/btc-weekly-move/download.py" "$SRC/btc-weekly-move/flash.py" "$SRC/run_misc_daily.sh" "$SRC/copy_to_dashboard.py"
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" "mkdir -p $DST/btc-weekly-move/output/json"
scp -i "$KEY" -o IdentitiesOnly=yes "$SRC/btc-weekly-move/download.py" "$SRC/btc-weekly-move/flash.py" "$HOST:$DST/btc-weekly-move/"
scp -i "$KEY" -o IdentitiesOnly=yes "$SRC/run_misc_daily.sh" "$SRC/copy_to_dashboard.py" "$HOST:$DST/"
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" bash -s <<'EOF'
set -euo pipefail
cd /root/data-download
set -a
[ -f .env ] && . ./.env
[ -f /root/dashboard/.env.live ] && . /root/dashboard/.env.live
set +a
./.venv/bin/python btc-weekly-move/download.py
./.venv/bin/python - <<'PY'
import json
doc=json.load(open("btc-weekly-move/output/json/btc_weekly_move.json"))
for w in doc["weeks"]:
    if w["week_end"]=="2026-08-23":
        print("AUG", round(w["start_px"]), round(w["end_px"]), round(w["usd"]), round(w["pct"],1), "usd_rank", w["usd_rank"], "pct_rank", w["pct_rank"], "best_pct", w["best_pct_since_2023_03"])
print("LATEST", doc["latest"]["week_end"], round(doc["latest"]["usd"]), "rank", doc["latest"]["usd_rank"])
PY
./.venv/bin/python copy_to_dashboard.py --dest /root/dashboard/public/json --only btc-weekly-move --force
./.venv/bin/python btc-weekly-move/flash.py --push --history-scan
EOF
