#!/usr/bin/env bash
# 安全同步本地 data-download 代码到 VPS：不 --delete，不覆盖线上数据落盘。
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/
DST=/root/data-download/
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=25)
RSYNC_SSH="ssh -i $KEY -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=25"

echo "[sync] code -> $HOST:$DST (no delete, skip output/state/env)"
rsync -avz \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.venv/' \
  --exclude 'node_modules/' \
  --exclude 'output/' \
  --exclude '**/output/' \
  --exclude 'cache/' \
  --exclude '**/cache/' \
  --exclude '*.log' \
  --exclude 'state.json' \
  --exclude '**/state.json' \
  --exclude '*_lark.json' \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude '_*.txt' \
  --exclude '*.pdf' \
  --exclude '*.xlsx' \
  --exclude '.git/' \
  --exclude '备份/' \
  --exclude '202609*/' \
  --exclude '**/monthly-report/traffic/' \
  -e "$RSYNC_SSH" \
  "$SRC" "$HOST:$DST"

echo "[sync] strip CRLF on key shells"
"${SSH[@]}" "$HOST" 'cd /root/data-download
python3 - <<'"'"'PY'"'"'
from pathlib import Path
roots = [Path(".")]
n = 0
for root in roots:
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in {".sh", ".py"} and p.name not in {"run_misc_daily.sh", "run_monitor.sh"}:
            if p.suffix not in {".sh", ".py"}:
                continue
        if "output" in p.parts or "__pycache__" in p.parts or ".venv" in p.parts:
            continue
        data = p.read_bytes()
        if b"\r" not in data:
            continue
        p.write_bytes(data.replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
        n += 1
print(f"crlf_fixed={n}")
PY
grep -n "monthly_flash\|hl-cex-share\|write_latest_json\|spot-break-liq/latest" run_misc_daily.sh spot-break-liq/run_monitor.sh spot-break-liq/monitor.py physical-tcg/monthly_flash.py perp-volume/hl_cex_share_flash.py bitmine-eth/download.py 2>/dev/null | head -40
'
echo SYNC_CODE_OK
