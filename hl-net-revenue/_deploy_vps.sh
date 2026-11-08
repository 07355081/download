#!/usr/bin/env bash
set -euo pipefail
KEY="${KEY:-$HOME/.ssh/data-dashboard-prod}"
HOST=root@47.74.6.207
MOD=/mnt/e/Code/data-download/hl-net-revenue
DD=/mnt/e/Code/data-download
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=25)
SCP=(scp -i "$KEY" -o IdentitiesOnly=yes)

"${SSH[@]}" "$HOST" 'mkdir -p /root/data-download/hl-net-revenue/output/json'
"${SCP[@]}" "$MOD/download.py" "$MOD/flash.py" "$MOD/_patch_feed.js" "$HOST:/root/data-download/hl-net-revenue/"
"${SCP[@]}" "$DD/run_misc_daily.sh" "$HOST:/root/data-download/"
"${SSH[@]}" "$HOST" 'sed -i "s/\r$//" /root/data-download/hl-net-revenue/download.py /root/data-download/hl-net-revenue/flash.py /root/data-download/hl-net-revenue/_patch_feed.js /root/data-download/run_misc_daily.sh'
echo "== download =="
"${SSH[@]}" "$HOST" 'cd /root/data-download && .venv/bin/python hl-net-revenue/download.py'
echo "== mirror =="
"${SSH[@]}" "$HOST" 'mkdir -p /root/dashboard/public/json/hl-net-revenue && cp -a /root/data-download/hl-net-revenue/output/json/hl_net_revenue.json /root/dashboard/public/json/hl-net-revenue/hl_net_revenue.json'
echo "== flash =="
"${SSH[@]}" "$HOST" 'cd /root/data-download && set -a && [ -f .env ] && . ./.env; [ -f /root/dashboard/.env.live ] && . /root/dashboard/.env.live; set +a; .venv/bin/python hl-net-revenue/flash.py --push'
echo "== patch feed =="
"${SSH[@]}" "$HOST" 'docker cp /root/data-download/hl-net-revenue/_patch_feed.js dashboard:/tmp/_patch_feed.js && docker exec dashboard node /tmp/_patch_feed.js'
echo "== done =="
