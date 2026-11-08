#!/usr/bin/env bash
set -euo pipefail
KEY="${HOME}/.ssh/data-dashboard-prod"
HOST=root@47.74.6.207
for i in 1 2 3 4 5 6 8 10; do
  if ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=15 "$HOST" "echo OK"; then
    exec bash /mnt/e/Code/data-download/tokenized-treasury-products/_deploy_vps.sh
  fi
  echo "retry $i"
  sleep "$i"
done
echo "SSH still down" >&2
exit 1
