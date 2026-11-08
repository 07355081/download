#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
DD=/mnt/e/Code/data-download

scp -o ConnectTimeout=25 -o IdentitiesOnly=yes -i "$KEY" \
  "$DD/stablecoin/download_defillama.py" \
  "$DD/stablecoin/usat_flash.py" \
  "$HOST:/root/data-download/stablecoin/"
scp -o ConnectTimeout=25 -o IdentitiesOnly=yes -i "$KEY" \
  "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/run_misc_daily.sh"

# dashboard panel + report-rules if tree exists on VPS
if ssh -o ConnectTimeout=25 -o IdentitiesOnly=yes -i "$KEY" "$HOST" \
  'test -f /root/dashboard/src/components/dashboard/StablecoinMarketCapPanel.tsx'; then
  scp -o ConnectTimeout=25 -o IdentitiesOnly=yes -i "$KEY" \
    /mnt/e/Code/data-dashboard/src/components/dashboard/StablecoinMarketCapPanel.tsx \
    "$HOST:/root/dashboard/src/components/dashboard/StablecoinMarketCapPanel.tsx" || true
  scp -o ConnectTimeout=25 -o IdentitiesOnly=yes -i "$KEY" \
    /mnt/e/Code/data-dashboard/src/lib/reports/report-rules.ts \
    "$HOST:/root/dashboard/src/lib/reports/report-rules.ts" || true
fi

ssh -o ConnectTimeout=25 -o IdentitiesOnly=yes -i "$KEY" "$HOST" bash -s <<'EOF'
set -euo pipefail
sed -i 's/\r$//' /root/data-download/stablecoin/download_defillama.py \
  /root/data-download/stablecoin/usat_flash.py \
  /root/data-download/run_misc_daily.sh
cd /root/data-download
.venv/bin/python stablecoin/download_defillama.py --only-coins usat
.venv/bin/python stablecoin/csv_to_json.py
.venv/bin/python copy_to_dashboard.py --dest /root/dashboard/public/json --only stablecoin --force
.venv/bin/python stablecoin/usat_flash.py --push
grep -n usat run_misc_daily.sh | head
EOF
echo DEPLOY_USAT_OK
