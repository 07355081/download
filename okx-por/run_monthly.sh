#!/bin/bash
# OKX PoR 月更：拉最新快照；新一期推日常，过线推阈值。
# 建议 crontab（上海时间，对齐官方约月初发布）：
#   40 10 3-12 * * /root/data-download/okx-por/run_monthly.sh
set -o pipefail
cd /root/data-download || exit 1
VENV=./.venv/bin/python
LOG=/root/data-download/okx-por/_monthly.log
ts() { date '+%Y-%m-%d %H:%M:%S'; }

{
  echo "======== [$(ts)] okx-por ========"
  $VENV okx-por/flash.py --refresh --push
  $VENV - <<'PY' || true
from pathlib import Path
import shutil
src = Path('/root/data-download/okx-por/output/json')
dst = Path('/root/dashboard/public/json/okx-por')
if src.is_dir():
    dst.mkdir(parents=True, exist_ok=True)
    latest = src / 'okx_por_latest.json'
    if latest.is_file():
        shutil.copy2(latest, dst / 'okx_por_latest.json')
    hist_src = src / 'okx-por-history'
    hist_dst = dst / 'okx-por-history'
    if hist_src.is_dir():
        hist_dst.mkdir(parents=True, exist_ok=True)
        for p in hist_src.glob('????-??-??.json'):
            shutil.copy2(p, hist_dst / p.name)
    print('copied to', dst)
    import sys
    sys.path.insert(0, '/root/data-download')
    from copy_to_dashboard import write_por_usdt_mirror
    mirrored = write_por_usdt_mirror(dst / 'okx-por-history', dst / 'latest.json')
    print('por mirror', mirrored)
PY
  echo "[$(ts)] done"
} >>"$LOG" 2>&1
