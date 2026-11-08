#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
DD=/mnt/e/Code/data-download
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=40)
SCP=(scp -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=40)

"${SCP[@]}" "$DD/gsr-core3/flash.py" "$DD/gsr-core3/chart.html" "$HOST:/root/data-download/gsr-core3/"
"${SSH[@]}" "$HOST" 'python3 -c "from pathlib import Path
for p in [Path(\"/root/data-download/gsr-core3/flash.py\")]:
 b=p.read_bytes(); p.write_bytes(b.replace(b\"\\r\\n\",b\"\\n\").replace(b\"\\r\",b\"\\n\"))
"
mkdir -p /root/dashboard/public/json/gsr-core3
cp -a /root/data-download/gsr-core3/chart.html /root/dashboard/public/json/gsr-core3/chart.html
# 改 cron：走 Python flash（带看图页），避免等 ACR
cat > /root/dashboard/scripts/run-gsr-core3-cron.sh << "EOF"
#!/bin/bash
# GSR Core3 weekly weights via data-download flash (Lark footer = own chart.html).
set -euo pipefail
LOG_FILE="${LOG_FILE:-/var/log/dashboard-gsr-core3-cron.log}"
{
  echo "[$(date "+%F %T")] start python flash"
  cd /root/data-download
  .venv/bin/python gsr-core3/flash.py --push
  echo "[$(date "+%F %T")] done"
} >>"$LOG_FILE" 2>&1
EOF
chmod +x /root/dashboard/scripts/run-gsr-core3-cron.sh
# bootstrap state from already-sent week so we do not re-push today
python3 - << "PY"
import json
from pathlib import Path
p = Path("/root/data-download/gsr-core3/output/gsr_core3_lark.json")
p.parent.mkdir(parents=True, exist_ok=True)
# mark latest week sent if json exists
doc = json.loads(Path("/root/dashboard/public/json/gsr_core3.json").read_text())
weeks = [w for w in doc.get("weeks") or [] if isinstance(w, dict) and w.get("date")]
weeks.sort(key=lambda w: w["date"])
latest = weeks[-1]["date"] if weeks else None
state = {"sentIds": [f"gsr-core3-{latest}"] if latest else [], "updatedAt": "bootstrap"}
p.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
print("state", state)
PY
# dry-run
cd /root/data-download && .venv/bin/python gsr-core3/flash.py | tail -5
curl -s -o /dev/null -w "paradex:%{http_code}\n" https://data.wublock123.com/json/paradex/chart.html
curl -s -o /dev/null -w "pm:%{http_code} " https://data.wublock123.com/dashboard/prediction-markets
curl -s -o /dev/null -w "core3:%{http_code}\n" https://data.wublock123.com/json/gsr-core3/chart.html
echo CORE3_FLASH_OK
'
