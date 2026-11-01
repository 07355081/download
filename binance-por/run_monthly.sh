#!/bin/bash
# Binance PoR 月更：拉最新快照，过线才推阈值，新一期推日常。
# 建议 crontab（上海时间月初错开，官方通常 1 日快照、约 7 日前发布）：
#   30 10 3-10 * * /root/data-download/binance-por/run_monthly.sh
set -o pipefail
cd /root/data-download || exit 1
VENV=./.venv/bin/python
LOG=/root/data-download/binance-por/_monthly.log
ts() { date '+%Y-%m-%d %H:%M:%S'; }

{
  echo "======== [$(ts)] binance-por ========"
  $VENV binance-por/flash.py --refresh --push
  # 同步到看板 json（只拷 PoR 文件，失败不阻断推送）
  $VENV - <<'PY' || true
from pathlib import Path
import shutil
src = Path('/root/data-download/binance-por/output/json')
dst = Path('/root/dashboard/public/json/binance-por')
if src.is_dir():
    dst.mkdir(parents=True, exist_ok=True)
    latest = src / 'binance_por_latest.json'
    if latest.is_file():
        shutil.copy2(latest, dst / 'binance_por_latest.json')
    hist_src = src / 'binance-por-history'
    hist_dst = dst / 'binance-por-history'
    if hist_src.is_dir():
        hist_dst.mkdir(parents=True, exist_ok=True)
        for p in hist_src.glob('????-??-??.json'):
            shutil.copy2(p, hist_dst / p.name)
    print('copied to', dst)
    import sys
    sys.path.insert(0, '/root/data-download')
    from copy_to_dashboard import write_por_usdt_mirror
    mirrored = write_por_usdt_mirror(dst / 'binance-por-history', dst / 'latest.json')
    print('por mirror', mirrored)
PY
  echo "[$(ts)] done"
} >>"$LOG" 2>&1
