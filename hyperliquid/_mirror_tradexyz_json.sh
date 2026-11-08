#!/bin/bash
set -eu
SRC=/root/data-download/hyperliquid/output/json/tradexyz_session.json
DEST_DIR=/root/dashboard/public/json/hyperliquid
mkdir -p "$DEST_DIR"
cp -a "$SRC" "$DEST_DIR/tradexyz_session.json"
ls -la "$DEST_DIR/tradexyz_session.json"
# if container has its own copy and the host path is not the mount, copy in
docker exec dashboard sh -c 'ls -la /app/public/json/hyperliquid/tradexyz_session.json' || {
  docker exec dashboard mkdir -p /app/public/json/hyperliquid
  docker cp "$DEST_DIR/tradexyz_session.json" dashboard:/app/public/json/hyperliquid/tradexyz_session.json
}
curl -s -o /dev/null -w "local:%{http_code}\n" http://127.0.0.1:3000/json/hyperliquid/tradexyz_session.json
python3 - <<'PY'
import json
from pathlib import Path
d=json.loads(Path("/root/dashboard/public/json/hyperliquid/tradexyz_session.json").read_text())
latest=d.get("latest") or {}
print("keys", list(d)[:12])
print("window", latest.get("window_end"), "outside", latest.get("outside_nyse_pct"), "closed", latest.get("closed_underlying_pct"))
print("returns", len(d.get("returns") or []), "bars", len(d.get("bars") or d.get("named") or []))
PY
