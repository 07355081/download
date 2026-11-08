#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/spot-break-liq
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=20)
SCP=(scp -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=20)

"${SSH[@]}" "$HOST" 'mkdir -p /root/data-download/spot-break-liq/output/json /root/dashboard/public/json/spot-break-liq'
"${SCP[@]}" "$SRC/monitor.py" "$SRC/run_monitor.sh" "$HOST:/root/data-download/spot-break-liq/"
"${SSH[@]}" "$HOST" 'python3 -c "
from pathlib import Path
for p in [\"/root/data-download/spot-break-liq/monitor.py\",\"/root/data-download/spot-break-liq/run_monitor.sh\"]:
    t=Path(p).read_bytes().replace(b\"\\r\\n\", b\"\\n\").replace(b\"\\r\", b\"\\n\")
    Path(p).write_bytes(t)
"
cd /root/data-download
./.venv/bin/python spot-break-liq/monitor.py
mkdir -p /root/dashboard/public/json/spot-break-liq
cp -a spot-break-liq/output/json/latest.json /root/dashboard/public/json/spot-break-liq/latest.json
ls -la /root/dashboard/public/json/spot-break-liq/latest.json
python3 -c "import json; d=json.load(open(\"/root/dashboard/public/json/spot-break-liq/latest.json\")); print(\"keys\",list(d)[:12]); print(\"assets\",len(d.get(\"assets\") or [])); print(\"series\",[(s[\"label\"],len(s.get(\"months\") or [])) for s in d.get(\"series\") or []]); print(\"liq\",d.get(\"liq_24h\"))"'
echo DEPLOY_SPOT_BREAK_LATEST_OK
