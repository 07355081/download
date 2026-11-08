#!/usr/bin/env bash
# 批量部署：Lark 文末改看板/看图 + chart.html + run_misc
set -euo pipefail
KEY="${KEY:-/root/.ssh/data-dashboard-prod}"
HOST=root@47.74.6.207
DD=/mnt/e/Code/data-download
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=40 -o ServerAliveInterval=10)
SCP=(scp -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=40 -o ServerAliveInterval=10)

CHART_MODS=(
  aave-v4-spokes gua-spot lab-spot zec-spot xrp-fees edgex-oi
  depin-mcap falconx-vault physical-tcg collector-crypt l2-token-mcap
)

FLASH_PATHS=(
  aave-v4-spokes/flash.py
  bianrensheng-spot/monitor.py
  binance-spcx-rank/flash.py
  bnb-spot/monitor.py
  btc-etf-volume/flash.py
  btc-hashrate-puell/flash.py
  btc-weekly-move/flash.py
  cb-premium/flash.py
  collector-crypt/flash.py
  depin-mcap/flash.py
  edgex-oi/flash.py
  etf-halfyear/flash.py
  eth-quarter/flash.py
  falconx-vault/flash.py
  financing-early-stage/flash.py
  gua-spot/flash.py
  hype-spot/monitor.py
  hyperliquid/tradexyz_oi_flash.py
  kr-cex-volume/flash.py
  l2-token-mcap/flash.py
  lab-spot/flash.py
  perp-dex-share/flash.py
  perp-volume/hl_cex_share_flash.py
  physical-tcg/flash.py
  physical-tcg/monthly_flash.py
  pm-weekly/flash.py
  prop-amm/flash.py
  rh-meme-mcap/flash.py
  rlusd-chain/flash.py
  spacex-liquidity/monitor.py
  spacex-perp/monitor.py
  spcx-liq/flash.py
  strc-apxusd/flash.py
  strive-btc/flash.py
  tokenized-treasuries/flash.py
  xrp-fees/flash.py
  zec-spot/flash.py
)

retry() {
  local n=0
  until "$@"; do
    n=$((n + 1))
    if [[ $n -ge 4 ]]; then
      echo "FAILED: $*" >&2
      return 1
    fi
    echo "retry $n: $*" >&2
    sleep 8
  done
}

echo "[1] mkdir remote"
retry "${SSH[@]}" "$HOST" "mkdir -p /root/data-download /root/dashboard/public/json $(printf '/root/data-download/%s ' "${CHART_MODS[@]}") $(printf '/root/dashboard/public/json/%s ' "${CHART_MODS[@]}")"

echo "[2] tar upload flashes+charts+misc"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bundle"
cp "$DD/run_misc_daily.sh" "$TMP/bundle/"
for rel in "${FLASH_PATHS[@]}"; do
  mkdir -p "$TMP/bundle/$(dirname "$rel")"
  cp "$DD/$rel" "$TMP/bundle/$rel"
done
for mod in "${CHART_MODS[@]}"; do
  mkdir -p "$TMP/bundle/$mod"
  cp "$DD/$mod/chart.html" "$TMP/bundle/$mod/chart.html"
done
tar -C "$TMP/bundle" -czf "$TMP/bundle.tgz" .
retry "${SCP[@]}" "$TMP/bundle.tgz" "$HOST:/tmp/lark_footer_bundle.tgz"

echo "[3] extract + CRLF + mirror charts"
retry "${SSH[@]}" "$HOST" 'set -e
tar -xzf /tmp/lark_footer_bundle.tgz -C /root/data-download
python3 - <<"PY"
from pathlib import Path
import shutil
root = Path("/root/data-download")
for p in list(root.rglob("*.py")) + [root / "run_misc_daily.sh"]:
    if not p.is_file():
        continue
    b = p.read_bytes()
    nb = b.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if nb != b:
        p.write_bytes(nb)
mods = "aave-v4-spokes gua-spot lab-spot zec-spot xrp-fees edgex-oi depin-mcap falconx-vault physical-tcg collector-crypt l2-token-mcap".split()
for mod in mods:
    src = root / mod / "chart.html"
    dst = Path("/root/dashboard/public/json") / mod / "chart.html"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_file():
        shutil.copy2(src, dst)
        print("chart", mod)
    else:
        print("missing", mod)
PY
rm -f /tmp/lark_footer_bundle.tgz
bash -n /root/data-download/run_misc_daily.sh
echo syntax_ok
grep -E "PAGE|DASHBOARD_URL|chart.html|dashboard/" /root/data-download/aave-v4-spokes/flash.py /root/data-download/cb-premium/flash.py | head -20
'

echo DEPLOY_LARK_FOOTER_OK
