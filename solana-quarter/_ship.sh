#!/usr/bin/env bash
set -euo pipefail
KEY="${KEY:-$HOME/.ssh/data-dashboard-prod}"
HOST=root@47.74.6.207
MOD=/mnt/e/Code/data-download/solana-quarter

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$MOD/download.py" "$MOD/flash.py" \
  "$HOST:/root/data-download/solana-quarter/"
scp -i "$KEY" -o IdentitiesOnly=yes \
  "$MOD/_patch_feeds.js" "$MOD/_add_series.py" \
  "$HOST:/tmp/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" 'sed -i "s/\r$//" /root/data-download/solana-quarter/download.py /root/data-download/solana-quarter/flash.py /tmp/_add_series.py'
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" 'docker cp /tmp/_patch_feeds.js dashboard:/tmp/_patch_feeds.js && docker exec dashboard node /tmp/_patch_feeds.js'
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" 'python3 /tmp/_add_series.py'
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" 'docker restart dashboard'
echo "== wait =="
sleep 8
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" 'curl -s -o /dev/null -w "page:%{http_code}\n" http://127.0.0.1:3000/dashboard/lark-feeds/solana-quarter; curl -s -o /dev/null -w "json:%{http_code}\n" http://127.0.0.1:3000/json/solana-quarter/solana_quarter.json'
echo "== flash =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" 'cd /root/data-download && set -a && [ -f .env ] && . ./.env; [ -f /root/dashboard/.env.live ] && . /root/dashboard/.env.live; set +a; .venv/bin/python solana-quarter/flash.py --push --force'
