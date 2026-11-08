#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=15 "$HOST" bash -s <<'EOF'
set -euo pipefail
python3 -c "
from pathlib import Path
for p in ['/root/data-download/x402/flash.py','/root/data-download/run_misc_daily.sh']:
    path=Path(p)
    path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n').replace(b'\r', b'\n'))
"
grep -n 'CHART_URL\|JSON_URL\|PAGE' /root/data-download/x402/flash.py | head
ls -la /root/dashboard/public/json/x402/
EOF
echo FIX_OK
