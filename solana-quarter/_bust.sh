#!/usr/bin/env bash
set -euo pipefail
KEY="${KEY:-$HOME/.ssh/data-dashboard-prod}"
scp -i "$KEY" -o IdentitiesOnly=yes /mnt/e/Code/data-download/solana-quarter/_bust.js root@47.74.6.207:/tmp/_bust.js
ssh -i "$KEY" -o IdentitiesOnly=yes root@47.74.6.207 'docker cp /tmp/_bust.js dashboard:/tmp/_bust.js && docker exec dashboard node /tmp/_bust.js && docker restart dashboard'
sleep 8
ssh -i "$KEY" -o IdentitiesOnly=yes root@47.74.6.207 'curl -s -o /dev/null -w "local:%{http_code}\n" http://127.0.0.1:3000/dashboard/lark-feeds/solana-quarter; curl -s http://127.0.0.1:3000/dashboard/lark-feeds/solana-quarter | grep -o "page-2ba04f7908730339[^\"]*" | head'
