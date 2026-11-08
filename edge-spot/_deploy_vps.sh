#!/usr/bin/env bash
set -euo pipefail
KEY="${HOME}/.ssh/data-dashboard-prod"
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/edge-spot
DD=/mnt/e/Code/data-download
CTL=/tmp/dd-ssh-edgespot-%r@%h:%p

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
  'mkdir -p /root/data-download/edge-spot/output/json /root/dashboard/public/json/edge-spot'

scp -i "$KEY" -o IdentitiesOnly=yes -o ControlPath="$CTL" \
  "$SRC/download.py" "$SRC/flash.py" "$SRC/_patch_feed.js" \
  "$HOST:/root/data-download/edge-spot/"

scp -i "$KEY" -o IdentitiesOnly=yes -o ControlPath="$CTL" \
  "$DD/run_misc_daily.sh" "$HOST:/root/data-download/"
scp -i "$KEY" -o IdentitiesOnly=yes -o ControlPath="$CTL" \
  "$DD/spot-break-liq/run_monitor.sh" "$HOST:/root/data-download/spot-break-liq/"

ssh -i "$KEY" -o IdentitiesOnly=yes -o ControlPath="$CTL" "$HOST" '
python3 - <<PY
from pathlib import Path
for p in [
 "/root/data-download/edge-spot/download.py",
 "/root/data-download/edge-spot/flash.py",
 "/root/data-download/edge-spot/_patch_feed.js",
 "/root/data-download/run_misc_daily.sh",
 "/root/data-download/spot-break-liq/run_monitor.sh",
]:
 t=Path(p).read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"); Path(p).write_bytes(t)
print("lf ok")
PY
cd /root/data-download
.venv/bin/python edge-spot/download.py
mkdir -p /root/dashboard/public/json/edge-spot
cp -a edge-spot/output/json/edge_spot.json /root/dashboard/public/json/edge-spot/edge_spot.json
.venv/bin/python edge-spot/flash.py --push
CID=$(docker ps --filter name=dashboard --format "{{.ID}}" | head -1)
if [ -n "$CID" ]; then
  docker cp /root/data-download/edge-spot/_patch_feed.js "$CID":/tmp/_patch_edge_spot.js
  docker exec "$CID" node /tmp/_patch_edge_spot.js || true
  docker restart "$CID" || true
fi
echo DEPLOY_EDGE_SPOT_OK
'

ssh -i "$KEY" -o IdentitiesOnly=yes -o ControlPath="$CTL" -O exit "$HOST" 2>/dev/null || true
