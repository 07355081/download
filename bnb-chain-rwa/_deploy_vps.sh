#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
DD=/mnt/e/Code/data-download
SSH=(ssh -o ConnectTimeout=40 -o IdentitiesOnly=yes -o ServerAliveInterval=5 -i "$KEY")
SCP=(scp -o ConnectTimeout=40 -o IdentitiesOnly=yes -o ServerAliveInterval=5 -i "$KEY")

for i in 1 2 3; do
  if "${SSH[@]}" "$HOST" 'mkdir -p /root/data-download/bnb-chain-rwa'; then
    break
  fi
  echo "ssh mkdir retry $i"
  sleep 5
done

"${SCP[@]}" \
  "$DD/bnb-chain-rwa/download.py" \
  "$DD/bnb-chain-rwa/flash.py" \
  "$HOST:/root/data-download/bnb-chain-rwa/"
"${SCP[@]}" \
  "$DD/run_misc_daily.sh" \
  "$HOST:/root/data-download/run_misc_daily.sh"

"${SSH[@]}" "$HOST" 'bash -s' <<'EOF'
set -euo pipefail
sed -i 's/\r$//' /root/data-download/bnb-chain-rwa/download.py \
  /root/data-download/bnb-chain-rwa/flash.py \
  /root/data-download/run_misc_daily.sh
cd /root/data-download
.venv/bin/python bnb-chain-rwa/download.py
mkdir -p /root/dashboard/public/json/bnb-chain-rwa
cp -a bnb-chain-rwa/output/json/bnb_chain_rwa.json \
  /root/dashboard/public/json/bnb-chain-rwa/bnb_chain_rwa.json
.venv/bin/python bnb-chain-rwa/flash.py --push
grep -n bnb-chain-rwa run_misc_daily.sh | head
EOF
echo DEPLOY_BNB_RWA_OK
