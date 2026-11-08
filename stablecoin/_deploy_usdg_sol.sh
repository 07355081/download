#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
DD=/mnt/e/Code/data-download

scp -o ConnectTimeout=25 -o IdentitiesOnly=yes -i "$KEY" \
  "$DD/stablecoin/usdg_sol_usde_flash.py" \
  "$HOST:/root/data-download/stablecoin/usdg_sol_usde_flash.py"
scp -o ConnectTimeout=25 -o IdentitiesOnly=yes -i "$KEY" \
  "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/run_misc_daily.sh"

ssh -o ConnectTimeout=25 -o IdentitiesOnly=yes -i "$KEY" "$HOST" bash -s <<'EOF'
set -euo pipefail
sed -i 's/\r$//' /root/data-download/stablecoin/usdg_sol_usde_flash.py /root/data-download/run_misc_daily.sh
cd /root/data-download
.venv/bin/python stablecoin/usdg_sol_usde_flash.py --push
grep -n usdg_sol_usde run_misc_daily.sh | head
EOF
echo DEPLOY_USDG_SOL_OK
