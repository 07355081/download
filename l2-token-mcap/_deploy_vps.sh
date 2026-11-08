#!/bin/bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download
DST=/root/data-download

sed -i 's/\r$//' \
  "$SRC/l2-token-mcap/download.py" \
  "$SRC/l2-token-mcap/flash.py" \
  "$SRC/run_misc_daily.sh" \
  "$SRC/copy_to_dashboard.py" \
  "$SRC/.env.example"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" "mkdir -p $DST/l2-token-mcap/output/json"

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SRC/l2-token-mcap/download.py" \
  "$SRC/l2-token-mcap/flash.py" \
  "$SRC/l2-token-mcap/README.md" \
  "$HOST:$DST/l2-token-mcap/"

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SRC/run_misc_daily.sh" \
  "$SRC/copy_to_dashboard.py" \
  "$SRC/.env.example" \
  "$HOST:$DST/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" bash -s <<'EOF'
set -euo pipefail
cd /root/data-download
set -a
[ -f .env ] && . ./.env
[ -f /root/dashboard/.env.live ] && . /root/dashboard/.env.live
set +a
echo "=== download ==="
./.venv/bin/python l2-token-mcap/download.py
echo "=== mirror ==="
./.venv/bin/python copy_to_dashboard.py --dest /root/dashboard/public/json --only l2-token-mcap --force
echo "=== flash push ==="
./.venv/bin/python l2-token-mcap/flash.py --push --history-scan
echo "=== done ==="
EOF
