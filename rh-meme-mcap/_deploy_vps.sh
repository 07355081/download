#!/usr/bin/env bash
set -euo pipefail
KEY="${KEY:-/mnt/c/Users/Administrator/.ssh/data-dashboard-prod}"
HOST=root@47.74.6.207
MOD=/mnt/e/Code/data-download/rh-meme-mcap
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=15 "$HOST" \
  'mkdir -p /root/data-download/rh-meme-mcap/output/json'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$MOD/download.py" "$MOD/flash.py" \
  "$HOST:/root/data-download/rh-meme-mcap/"

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'sed -i "s/\r$//" \
    /root/data-download/rh-meme-mcap/download.py \
    /root/data-download/rh-meme-mcap/flash.py \
    /root/data-download/run_misc_daily.sh \
    && chmod +x /root/data-download/run_misc_daily.sh'

echo "== download =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && set -a && [ -f .env ] && . ./.env; [ -f /root/dashboard/.env.live ] && . /root/dashboard/.env.live; set +a; .venv/bin/python rh-meme-mcap/download.py'

echo "== mirror json =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'mkdir -p /root/dashboard/public/json/rh-meme-mcap && cp -a /root/data-download/rh-meme-mcap/output/json/rh_meme_mcap.json /root/dashboard/public/json/rh-meme-mcap/rh_meme_mcap.json && ls -la /root/dashboard/public/json/rh-meme-mcap/'

echo "== flash push =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && set -a && [ -f .env ] && . ./.env; [ -f /root/dashboard/.env.live ] && . /root/dashboard/.env.live; set +a; .venv/bin/python rh-meme-mcap/flash.py --push'

echo "== done =="
