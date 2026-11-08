#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
DD=/mnt/e/Code/data-download
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=40)
SCP=(scp -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=40)

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/b"
# pack all rewritten flashes + gsr chart + run_misc
cd "$DD"
tar -czf "$TMP/flashes.tgz" \
  --exclude='*/output/*' --exclude='*/__pycache__/*' --exclude='*/.venv/*' \
  $(python3 - <<'PY'
from pathlib import Path
root = Path("/mnt/e/Code/data-download")
# files that mention lark-feeds or recently touched MAP
for p in root.rglob("*.py"):
    if p.name.startswith("_"): continue
    if any(x in p.parts for x in ("__pycache__", ".venv", "output")): continue
    if "flash" in p.name or p.name in ("monitor.py", "monitor_yzi_ena.py", "monitor_pump_sol.py"):
        t = p.read_text(encoding="utf-8", errors="ignore")
        if "lark-feeds" in t or "dashboard/burns" in t or "dashboard/etf" in t or "dashboard/x402" in t or "dashboard/tradfi" in t or "dashboard/robinhood" in t or "dashboard/stablecoin" in t:
            print(p.relative_to(root).as_posix())
PY
)
"${SCP[@]}" "$TMP/flashes.tgz" "$HOST:/tmp/lark_feeds_flashes.tgz"
"${SSH[@]}" "$HOST" 'set -e
cd /root/data-download
tar -xzf /tmp/lark_feeds_flashes.tgz
python3 - <<"PY"
from pathlib import Path
root=Path("/root/data-download")
n=0
for p in root.rglob("*.py"):
    b=p.read_bytes(); nb=b.replace(b"\r\n",b"\n").replace(b"\r",b"\n")
    if nb!=b:
        p.write_bytes(nb); n+=1
print("crlf", n)
# sample
for rel in ["paradex/flash.py","binance-por/flash.py","gsr-core3/flash.py","wallet-watch/monitor.py","aave-llama/flash.py"]:
    t=(root/rel).read_text(encoding="utf-8", errors="ignore")
    for line in t.splitlines():
        if "lark-feeds" in line or line.startswith("PAGE") or line.startswith("POR_PAGE") or line.startswith("DASHBOARD"):
            if "http" in line:
                print(rel, line.strip()[:100])
                break
PY
rm -f /tmp/lark_feeds_flashes.tgz
# update core3 cron PAGE already in flash
echo FLASHES_OK
'
