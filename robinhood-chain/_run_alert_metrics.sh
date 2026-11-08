#!/usr/bin/env bash
# One-shot: refresh RH alert metrics on VPS, then mirror to dashboard json.
set -euo pipefail
cd /root/data-download
VENV=/root/data-download/.venv/bin/python

echo "=== credit budget (before) ==="
$VENV _dune.py || true

echo "=== download alert metrics (force execute owned + active_wallets) ==="
# Force so stale Oct-4 caches actually re-run. Caps still enforced in _dune.
$VENV robinhood-chain/download.py --only daily_transactions --force-execute
$VENV robinhood-chain/download.py --only dex_volume --force-execute
$VENV robinhood-chain/download.py --only active_wallets --force-execute

echo "=== copy to dashboard ==="
$VENV copy_to_dashboard.py --dest /root/dashboard/public/json --only robinhood-chain --force

echo "=== date ranges ==="
$VENV -c '
import json
from pathlib import Path
root = Path("robinhood-chain/output/json")
for name in ["daily_transactions", "active_wallets", "dex_volume"]:
    p = root / f"{name}.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    rows = d.get("data") or []
    days = sorted({str(r.get("day"))[:10] for r in rows if isinstance(r, dict) and r.get("day")})
    print(name, "n=", len(days), "range", (days[0] if days else None), "->", (days[-1] if days else None), "qid=", d.get("query_id"), "updated=", d.get("updated_at"))
'

echo "=== credit budget (after) ==="
$VENV _dune.py || true
echo "DONE_OK"
