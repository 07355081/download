#!/usr/bin/env bash
set -euo pipefail
KEY="${KEY:-$HOME/.ssh/data-dashboard-prod}"
HOST=root@47.74.6.207
MOD=/mnt/e/Code/data-download/pm-weekly
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=15 "$HOST" \
  'mkdir -p /root/data-download/pm-weekly/output/json'
scp -i "$KEY" -o IdentitiesOnly=yes \
  "$MOD/download.py" "$MOD/flash.py" \
  "$HOST:/root/data-download/pm-weekly/"
scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/"
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'sed -i "s/\r$//" \
    /root/data-download/pm-weekly/download.py \
    /root/data-download/pm-weekly/flash.py \
    /root/data-download/run_misc_daily.sh'
echo "== download =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python pm-weekly/download.py'
echo "== mirror =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'mkdir -p /root/dashboard/public/json/pm-weekly && cp -a /root/data-download/pm-weekly/output/json/pm_weekly.json /root/dashboard/public/json/pm-weekly/pm_weekly.json'
echo "== flash =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && set -a && [ -f .env ] && . ./.env; [ -f /root/dashboard/.env.live ] && . /root/dashboard/.env.live; set +a; .venv/bin/python pm-weekly/flash.py --push'
echo "== done =="
