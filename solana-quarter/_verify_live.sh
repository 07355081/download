#!/usr/bin/env bash
set -euo pipefail
KEY="${KEY:-$HOME/.ssh/data-dashboard-prod}"
scp -i "$KEY" -o IdentitiesOnly=yes /mnt/e/Code/data-download/solana-quarter/_verify_live.js root@47.74.6.207:/tmp/_verify_live.js
ssh -i "$KEY" -o IdentitiesOnly=yes root@47.74.6.207 'docker cp /tmp/_verify_live.js dashboard:/tmp/_verify_live.js && docker exec dashboard node /tmp/_verify_live.js'
echo "---- json ----"
ssh -i "$KEY" -o IdentitiesOnly=yes root@47.74.6.207 "grep -F labelEn /root/dashboard/public/json/solana-quarter/solana_quarter.json"
