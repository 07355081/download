#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/okx-por

ssh -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$HOST" \
  'mkdir -p /root/data-download/okx-por/output/json'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SRC/download.py" "$SRC/flash.py" "$SRC/run_monthly.sh" "$SRC/README.md" \
  "$HOST:/root/data-download/okx-por/"

if [[ -d "$SRC/output/json/okx-por-history" ]]; then
  scp -i "$KEY" -o IdentitiesOnly=yes -r \
    "$SRC/output/json/okx-por-history" \
    "$HOST:/root/data-download/okx-por/output/json/"
fi

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" bash -s <<'REMOTE'
set -euo pipefail
sed -i 's/\r$//' /root/data-download/okx-por/*.py /root/data-download/okx-por/*.sh
chmod +x /root/data-download/okx-por/run_monthly.sh
cd /root/data-download
.venv/bin/python okx-por/download.py
.venv/bin/python okx-por/flash.py
# crontab: add monthly if missing
CRON_LINE='40 10 3-12 * * /root/data-download/okx-por/run_monthly.sh'
(crontab -l 2>/dev/null | grep -v 'okx-por/run_monthly.sh'; echo "$CRON_LINE") | crontab -
echo '--- crontab okx-por ---'
crontab -l | grep okx-por || true
# copy json to dashboard
mkdir -p /root/dashboard/public/json/okx-por/okx-por-history
cp -a /root/data-download/okx-por/output/json/okx_por_latest.json /root/dashboard/public/json/okx-por/ 2>/dev/null || true
cp -a /root/data-download/okx-por/output/json/okx-por-history/*.json /root/dashboard/public/json/okx-por/okx-por-history/ 2>/dev/null || true
ls -la /root/dashboard/public/json/okx-por/ || true
REMOTE

echo DEPLOY_OKX_POR_VPS_OK
