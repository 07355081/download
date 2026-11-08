#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/physical-tcg
DD=/mnt/e/Code/data-download
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=20)
SCP=(scp -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=20)

"${SSH[@]}" "$HOST" 'mkdir -p /root/data-download/physical-tcg/output/json /root/dashboard/public/json/physical-tcg'
"${SCP[@]}" "$SRC/monthly_flash.py" "$HOST:/root/data-download/physical-tcg/"
"${SCP[@]}" "$DD/run_misc_daily.sh" "$HOST:/root/data-download/"
"${SSH[@]}" "$HOST" 'python3 -c "
from pathlib import Path
for p in [\"/root/data-download/physical-tcg/monthly_flash.py\",\"/root/data-download/run_misc_daily.sh\"]:
    t=Path(p).read_bytes().replace(b\"\\r\\n\", b\"\\n\").replace(b\"\\r\", b\"\\n\")
    Path(p).write_bytes(t)
"'
"${SSH[@]}" "$HOST" 'cd /root/data-download && .venv/bin/python physical-tcg/monthly_flash.py --skip-fetch; mkdir -p /root/dashboard/public/json/physical-tcg; cp -a physical-tcg/output/json/physical_tcg_monthly.json /root/dashboard/public/json/physical-tcg/ 2>/dev/null || true; grep -n monthly_flash run_misc_daily.sh | head'
echo DEPLOY_PHYSICAL_TCG_MONTHLY_OK
