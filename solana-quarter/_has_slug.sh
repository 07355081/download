#!/usr/bin/env bash
set -euo pipefail
KEY="${KEY:-$HOME/.ssh/data-dashboard-prod}"
scp -i "$KEY" -o IdentitiesOnly=yes /mnt/e/Code/data-download/solana-quarter/_has_slug.js root@47.74.6.207:/tmp/_has_slug.js
ssh -i "$KEY" -o IdentitiesOnly=yes root@47.74.6.207 'docker cp /tmp/_has_slug.js dashboard:/tmp/_has_slug.js && docker exec dashboard node /tmp/_has_slug.js'
