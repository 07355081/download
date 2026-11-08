#!/usr/bin/env bash
set -euo pipefail
KEY="${KEY:-$HOME/.ssh/data-dashboard-prod}"
scp -i "$KEY" -o IdentitiesOnly=yes /mnt/e/Code/data-download/solana-quarter/_snip_fmt.js root@47.74.6.207:/tmp/_snip_fmt.js
ssh -i "$KEY" -o IdentitiesOnly=yes root@47.74.6.207 'docker cp /tmp/_snip_fmt.js dashboard:/tmp/_snip_fmt.js && docker exec dashboard node /tmp/_snip_fmt.js'
