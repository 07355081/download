#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
DD=/mnt/e/Code/data-download
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=40 -o ServerAliveInterval=10)
SCP=(scp -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=40)

MODS=(paradex fragment near-intents spark-savings cb-stock-dex cb-base-stock-dex chain-fees-dist chain-app-revenue gsr-core3)
FLASHES=(
  paradex/flash.py fragment/flash.py near-intents/flash.py spark-savings/flash.py
  cb-stock-dex/flash.py cb-base-stock-dex/flash.py chain-fees-dist/flash.py
  chain-app-revenue/flash.py cb-premium/flash.py
)

retry() { local n=0; until "$@"; do n=$((n+1)); [[ $n -ge 4 ]] && return 1; sleep 8; done; }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/b"
cp "$DD/run_misc_daily.sh" "$TMP/b/"
for rel in "${FLASHES[@]}"; do
  mkdir -p "$TMP/b/$(dirname "$rel")"
  cp "$DD/$rel" "$TMP/b/$rel"
done
for mod in "${MODS[@]}"; do
  mkdir -p "$TMP/b/$mod"
  cp "$DD/$mod/chart.html" "$TMP/b/$mod/"
done
# also mirror cb-stock json if local exists
for p in cb-stock-dex/output/json/cb_stock_dex.json cb-base-stock-dex/output/json/daily.json; do
  if [[ -f "$DD/$p" ]]; then
    mkdir -p "$TMP/b/$(dirname "$p")"
    cp "$DD/$p" "$TMP/b/$p"
  fi
done
tar -C "$TMP/b" -czf "$TMP/b.tgz" .

retry "${SCP[@]}" "$TMP/b.tgz" "$HOST:/tmp/third_footers.tgz"
retry "${SSH[@]}" "$HOST" 'set -e
tar -xzf /tmp/third_footers.tgz -C /root/data-download
python3 - <<"PY"
from pathlib import Path
import shutil
root = Path("/root/data-download")
for p in list(root.rglob("*.py")) + [root/"run_misc_daily.sh"]:
    if p.is_file():
        b=p.read_bytes(); nb=b.replace(b"\r\n",b"\n").replace(b"\r",b"\n")
        if nb!=b: p.write_bytes(nb)
mods = "paradex fragment near-intents spark-savings cb-stock-dex cb-base-stock-dex chain-fees-dist chain-app-revenue gsr-core3".split()
pub = Path("/root/dashboard/public/json")
for mod in mods:
    src = root/mod/"chart.html"
    dst = pub/mod/"chart.html"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_file():
        shutil.copy2(src, dst)
        print("chart", mod)
# mirror stock json if present in tarball
for rel, dest in [
    ("cb-stock-dex/output/json/cb_stock_dex.json", "cb-stock-dex/cb_stock_dex.json"),
    ("cb-base-stock-dex/output/json/daily.json", "cb-base-stock-dex/daily.json"),
]:
    s = root/rel
    if s.is_file():
        d = pub/dest
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
        print("json", dest)
# copy gsr chart only; json already at public/json/gsr_core3.json
PY
bash -n /root/data-download/run_misc_daily.sh
grep -E "PAGE|DASHBOARD_URL" /root/data-download/paradex/flash.py /root/data-download/pm-weekly/flash.py /root/data-download/cb-premium/flash.py | head -20
rm -f /tmp/third_footers.tgz
echo DEPLOY_THIRD_OK
'
