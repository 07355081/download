#!/usr/bin/env bash
set -euo pipefail
KEY="${KEY:-$HOME/.ssh/data-dashboard-prod}"
HOST=root@47.74.6.207
MOD=/mnt/e/Code/data-download/strive-btc
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=15 "$HOST" \
  'mkdir -p /root/data-download/strive-btc/output/json'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$MOD/download.py" "$MOD/flash.py" \
  "$HOST:/root/data-download/strive-btc/"

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'sed -i "s/\r$//" \
    /root/data-download/strive-btc/download.py \
    /root/data-download/strive-btc/flash.py \
    /root/data-download/run_misc_daily.sh \
    && chmod +x /root/data-download/run_misc_daily.sh'

echo "== download =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && set -a && [ -f .env ] && . ./.env; [ -f /root/dashboard/.env.live ] && . /root/dashboard/.env.live; set +a; .venv/bin/python strive-btc/download.py'

echo "== mirror json =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'mkdir -p /root/dashboard/public/json/strive-btc && cp -a /root/data-download/strive-btc/output/json/strive_btc.json /root/dashboard/public/json/strive-btc/strive_btc.json'

echo "== flash push =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && set -a && [ -f .env ] && . ./.env; [ -f /root/dashboard/.env.live ] && . /root/dashboard/.env.live; set +a; .venv/bin/python strive-btc/flash.py --push'

echo "== done =="
