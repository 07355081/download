#!/usr/bin/env bash
set -euo pipefail
KEY="${KEY:-$HOME/.ssh/data-dashboard-prod}"
ssh -i "$KEY" -o IdentitiesOnly=yes root@47.74.6.207 'docker restart dashboard'
sleep 8
ssh -i "$KEY" -o IdentitiesOnly=yes root@47.74.6.207 'curl -s -o /dev/null -w "local:%{http_code}\n" http://127.0.0.1:3000/dashboard/lark-feeds/solana-quarter'
