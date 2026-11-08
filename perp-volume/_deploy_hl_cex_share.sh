#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/perp-volume
DD=/mnt/e/Code/data-download

SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=20)
SCP=(scp -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=20)

"${SSH[@]}" "$HOST" 'mkdir -p /root/data-download/perp-volume/output'

"${SCP[@]}" "$SRC/hl_cex_share_flash.py" "$HOST:/root/data-download/perp-volume/"
"${SCP[@]}" "$DD/run_misc_daily.sh" "$HOST:/root/data-download/"

"${SSH[@]}" "$HOST" 'python3 -c "
from pathlib import Path
for p in [\"/root/data-download/perp-volume/hl_cex_share_flash.py\",\"/root/data-download/run_misc_daily.sh\"]:
    t=Path(p).read_bytes().replace(b\"\\r\\n\", b\"\\n\").replace(b\"\\r\", b\"\\n\")
    Path(p).write_bytes(t)
"'

"${SSH[@]}" "$HOST" 'cd /root/data-download && .venv/bin/python perp-volume/hl_cex_share_flash.py && grep -n hl-cex-share run_misc_daily.sh'

echo DEPLOY_HL_CEX_SHARE_OK
