#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/wallet-watch

scp -o ConnectTimeout=25 -o IdentitiesOnly=yes -i "$KEY" \
  "$SRC/monitor_yzi_ena.py" "$SRC/run_hourly.sh" \
  "$HOST:/root/data-download/wallet-watch/"

ssh -o ConnectTimeout=25 -o IdentitiesOnly=yes -i "$KEY" "$HOST" bash <<'EOF'
set -euo pipefail
sed -i 's/\r$//' /root/data-download/wallet-watch/monitor_yzi_ena.py /root/data-download/wallet-watch/run_hourly.sh
chmod +x /root/data-download/wallet-watch/run_hourly.sh
cd /root/data-download
.venv/bin/python wallet-watch/monitor_yzi_ena.py --bootstrap
.venv/bin/python wallet-watch/monitor_yzi_ena.py --push
EOF
echo DEPLOY_YZI_ENA_OK
