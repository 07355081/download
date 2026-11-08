#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/okx-por
OUT=/mnt/e/Code/data-download/okx-por/_vps_debug.txt

{
  echo "=== probe ==="
  scp -i "$KEY" -o IdentitiesOnly=yes "$SRC/_probe_page.py" "$HOST:/tmp/_probe_page.py"
  ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
    '/root/data-download/.venv/bin/python /tmp/_probe_page.py'
  echo "=== latest json ==="
  ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
    'python3 -c "import json; d=json.load(open(\"/root/data-download/okx-por/output/json/okx_por_latest.json\")); print(json.dumps(d,ensure_ascii=False,indent=2)[:3000])"'
  echo "=== html head markers ==="
  ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
    'wc -c /tmp/okx_por.html; rg -n "priapi|reserveRatio|账户资产|139,?865|__NEXT_DATA__|502299735" /tmp/okx_por.html | head -40 || grep -nE "priapi|reserveRatio|账户资产|139|__NEXT_DATA__|502299735" /tmp/okx_por.html | head -40'
} >"$OUT" 2>&1

echo wrote "$OUT"
