#!/usr/bin/env bash
set -euo pipefail
KEY="${KEY:-$HOME/.ssh/data-dashboard-prod}"
HOST=root@47.74.6.207
MOD=/mnt/e/Code/data-download/prediction-markets
DD=/mnt/e/Code/data-download
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=25)
SCP=(scp -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=25)

"${SSH[@]}" "$HOST" 'mkdir -p /root/data-download/prediction-markets/output/json'
"${SCP[@]}" "$MOD/monthly_flash.py" "$HOST:/root/data-download/prediction-markets/"
"${SCP[@]}" "$DD/run_misc_daily.sh" "$HOST:/root/data-download/"
"${SSH[@]}" "$HOST" 'python3 -c "
from pathlib import Path
for p in [
    \"/root/data-download/prediction-markets/monthly_flash.py\",
    \"/root/data-download/run_misc_daily.sh\",
]:
    t = Path(p).read_bytes().replace(b\"\\r\\n\", b\"\\n\").replace(b\"\\r\", b\"\\n\")
    Path(p).write_bytes(t)
print(\"lf ok\")
"'
echo "== download =="
"${SSH[@]}" "$HOST" 'cd /root/data-download && .venv/bin/python prediction-markets/download.py'
echo "== mirror =="
"${SSH[@]}" "$HOST" 'cp -a /root/data-download/prediction-markets/output/json/prediction_markets_quarterly.json /root/dashboard/public/json/prediction_markets_quarterly.json'
echo "== flash dry =="
"${SSH[@]}" "$HOST" 'cd /root/data-download && .venv/bin/python prediction-markets/monthly_flash.py'
echo "== flash push =="
"${SSH[@]}" "$HOST" 'cd /root/data-download && set -a && [ -f .env ] && . ./.env; [ -f /root/dashboard/.env.live ] && . /root/dashboard/.env.live; set +a; .venv/bin/python prediction-markets/monthly_flash.py --push'
echo "== misc hook =="
"${SSH[@]}" "$HOST" 'grep -n monthly_flash /root/data-download/run_misc_daily.sh | head'
echo DEPLOY_PM_MONTHLY_OK
