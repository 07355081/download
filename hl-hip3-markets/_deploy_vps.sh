#!/usr/bin/env bash
set -euo pipefail
KEY="${KEY:-$HOME/.ssh/data-dashboard-prod}"
HOST=root@47.74.6.207
MOD=/mnt/e/Code/data-download/hl-hip3-markets
DD=/mnt/e/Code/data-download
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=25)
SCP=(scp -i "$KEY" -o IdentitiesOnly=yes)

"${SSH[@]}" "$HOST" 'mkdir -p /root/data-download/hl-hip3-markets/output/json'
"${SCP[@]}" "$MOD/download.py" "$MOD/flash.py" "$MOD/_patch_feed.js" \
  "$HOST:/root/data-download/hl-hip3-markets/"
"${SCP[@]}" "$MOD/output/json/hl_hip3_markets.json" \
  "$HOST:/root/data-download/hl-hip3-markets/output/json/hl_hip3_markets.json"
"${SCP[@]}" "$DD/run_misc_daily.sh" "$HOST:/root/data-download/"
"${SSH[@]}" "$HOST" 'sed -i "s/\r$//" /root/data-download/hl-hip3-markets/download.py /root/data-download/hl-hip3-markets/flash.py /root/data-download/hl-hip3-markets/_patch_feed.js /root/data-download/run_misc_daily.sh'
echo "== download =="
"${SSH[@]}" "$HOST" 'cd /root/data-download && .venv/bin/python hl-hip3-markets/download.py' \
  || echo "[warn] VPS download failed; keep seeded json"
echo "== mirror =="
"${SSH[@]}" "$HOST" 'mkdir -p /root/dashboard/public/json/hl-hip3-markets && cp -a /root/data-download/hl-hip3-markets/output/json/hl_hip3_markets.json /root/dashboard/public/json/hl-hip3-markets/hl_hip3_markets.json'
echo "== flash =="
"${SSH[@]}" "$HOST" 'cd /root/data-download && set -a && [ -f .env ] && . ./.env; [ -f /root/dashboard/.env.live ] && . /root/dashboard/.env.live; set +a; .venv/bin/python hl-hip3-markets/flash.py --push'
echo "== patch feed =="
"${SSH[@]}" "$HOST" 'docker cp /root/data-download/hl-hip3-markets/_patch_feed.js dashboard:/tmp/_patch_feed.js && docker exec dashboard node /tmp/_patch_feed.js && docker restart dashboard'
echo "== done =="
