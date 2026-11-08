#!/usr/bin/env bash
set -euo pipefail
KEY="${KEY:-$HOME/.ssh/data-dashboard-prod}"
ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=25 root@47.74.6.207 'bash -s' << 'EOF'
python3 - << 'PY'
import json
from pathlib import Path
p = Path("/var/lib/docker/overlay2")
# files are inside the container; use docker exec
print("skip")
PY
docker exec dashboard sh -c 'grep -o "page-[a-z0-9-]*hlrev.js" /app/.next/app-build-manifest.json | sort | uniq -c'
echo "---- slug manifest ----"
docker exec dashboard sh -c 'grep -o "hl-net-revenue\|page-[A-Za-z0-9_-]*\.js" /app/.next/server/app/dashboard/lark-feeds/\[slug\]/page_client-reference-manifest.js | sort | uniq -c | head -40'
echo "---- server page has slug ----"
docker exec dashboard sh -c 'grep -c hl-net-revenue /app/.next/server/app/dashboard/lark-feeds/\[slug\]/page.js || true'
EOF
