#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/bitmine-eth
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=20)
SCP=(scp -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=20)

"${SCP[@]}" "$SRC/download.py" "$HOST:/root/data-download/bitmine-eth/download.py"
"${SCP[@]}" "$SRC/output/json/bitmine_eth.json" "$HOST:/root/data-download/bitmine-eth/output/json/bitmine_eth.json"
"${SSH[@]}" "$HOST" 'python3 -c "
from pathlib import Path
p=Path(\"/root/data-download/bitmine-eth/download.py\")
p.write_bytes(p.read_bytes().replace(b\"\\r\\n\", b\"\\n\").replace(b\"\\r\", b\"\\n\"))
"
mkdir -p /root/dashboard/public/json/bitmine-eth
cp -a /root/data-download/bitmine-eth/output/json/bitmine_eth.json /root/dashboard/public/json/bitmine-eth/bitmine_eth.json
python3 -c "import json; d=json.load(open(\"/root/dashboard/public/json/bitmine-eth/bitmine_eth.json\")); r=d[\"history\"][-1]; print(r); assert \"value\" in r and r[\"value\"]==r[\"eth\"]"'
echo DEPLOY_BITMINE_CHART_JSON_OK
