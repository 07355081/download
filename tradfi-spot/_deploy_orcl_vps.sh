#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
TS=/mnt/e/Code/data-download/tradfi-spot

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=15 "$HOST" \
  'mkdir -p /root/data-download/tradfi-spot'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$TS/session_moves.py" "$TS/us_session_flash.py" \
  "$HOST:/root/data-download/tradfi-spot/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'sed -i "s/\r$//" /root/data-download/tradfi-spot/session_moves.py /root/data-download/tradfi-spot/us_session_flash.py'

echo "== basket check + refresh =="
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download/tradfi-spot && /root/data-download/.venv/bin/python - <<'"'"'PY'"'"'
from session_moves import SESSION_BASKET, write_session_moves, MOVES_PATH
from _common import Http
print("basket", len(SESSION_BASKET), [s["ticker"] for s in SESSION_BASKET])
assert any(s["ticker"] == "ORCL" for s in SESSION_BASKET)
http = Http()
ok = write_session_moves(http)
print("complete", ok, "path", MOVES_PATH)
import json
doc = json.loads(MOVES_PATH.read_text())
print("json complete", doc.get("complete"), "rows", len(doc.get("rows") or []))
print("tickers", [r.get("ticker") for r in (doc.get("rows") or [])])
PY'

# Prefer mirror if available
ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'test -f /root/data-download/tradfi-spot/output/tradfi-session-moves.json && cp -a /root/data-download/tradfi-spot/output/tradfi-session-moves.json /root/dashboard/public/json/tradfi-session-moves.json && echo copied_json || echo skip_copy'

echo DEPLOY_ORCL_OK
