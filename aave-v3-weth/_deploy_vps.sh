#!/usr/bin/env bash
set -euo pipefail
KEY="${HOME}/.ssh/data-dashboard-prod"
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/aave-v3-weth
DD=/mnt/e/Code/data-download
CTL=/tmp/dd-ssh-aaveweth-%r@%h:%p

for i in 1 2 3 4 5; do
  if ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=20 \
    -o ControlMaster=auto -o ControlPersist=180 -o ControlPath="$CTL" \
    "$HOST" 'echo OK'; then
    break
  fi
  echo "retry $i"
  sleep $((i * 3))
done

ssh -i "$KEY" -o IdentitiesOnly=yes -o ControlPath="$CTL" "$HOST" \
  'mkdir -p /root/data-download/aave-v3-weth/output/json /root/dashboard/public/json/aave-v3-weth'

scp -i "$KEY" -o IdentitiesOnly=yes -o ControlPath="$CTL" \
  "$SRC/download.py" "$SRC/flash.py" "$SRC/_patch_feed.js" \
  "$HOST:/root/data-download/aave-v3-weth/"

scp -i "$KEY" -o IdentitiesOnly=yes -o ControlPath="$CTL" \
  "$DD/run_misc_daily.sh" "$HOST:/root/data-download/"

ssh -i "$KEY" -o IdentitiesOnly=yes -o ControlPath="$CTL" "$HOST" '
python3 - <<PY
from pathlib import Path
for p in [
 "/root/data-download/aave-v3-weth/download.py",
 "/root/data-download/aave-v3-weth/flash.py",
 "/root/data-download/aave-v3-weth/_patch_feed.js",
 "/root/data-download/run_misc_daily.sh",
]:
 t=Path(p).read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"); Path(p).write_bytes(t)
print("lf ok")
PY
cd /root/data-download
.venv/bin/python aave-v3-weth/download.py
mkdir -p /root/dashboard/public/json/aave-v3-weth
cp -a aave-v3-weth/output/json/aave_v3_weth.json /root/dashboard/public/json/aave-v3-weth/aave_v3_weth.json
.venv/bin/python aave-v3-weth/flash.py --push
CID=$(docker ps --filter name=dashboard --format "{{.ID}}" | head -1)
if [ -n "$CID" ]; then
  docker cp /root/data-download/aave-v3-weth/_patch_feed.js "$CID":/tmp/_patch_aave_v3_weth.js
  docker exec "$CID" node /tmp/_patch_aave_v3_weth.js || true
  docker restart "$CID" || true
fi
echo DEPLOY_AAVE_V3_WETH_OK
'

ssh -i "$KEY" -o IdentitiesOnly=yes -o ControlPath="$CTL" -O exit "$HOST" 2>/dev/null || true
