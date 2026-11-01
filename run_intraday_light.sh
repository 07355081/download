#!/bin/bash
# 盘中轻量刷新（PaperDash / GMGN / Deribit DVOL / DefiLlama 稳定币）。
# 不拿 .pipeline.lock，不跑 gen_dashboard_meta，不镜像整份 Hyperliquid。
# 建议 crontab：15 * * * *  /usr/local/bin/safe-run /root/data-download/run_intraday_light.sh
# 日志：_intraday_light.log
set -o pipefail
cd /root/data-download || exit 1

VENV=./.venv/bin/python
LOG=/root/data-download/_intraday_light.log
DEST=/root/dashboard/public/json
LOCK=/root/data-download/.intraday_light.lock
ts() { date '+%Y-%m-%d %H:%M:%S'; }

# 自己的锁，避免上一小时还没跑完又叠一轮。不是三个日更管道共用的 .pipeline.lock。
exec 8>"$LOCK"
flock -n 8 || { echo "[$(ts)] skip: intraday lock busy" >> "$LOG"; exit 0; }

if ! $VENV _watermark.py --outbox /root/outbox >> "$LOG" 2>&1; then
  echo "[$(ts)] skip: watermark" >> "$LOG"
  exit 0
fi

{
  echo "==================== [$(ts)] intraday light 开始 ===================="

  echo "[$(ts)] paperdash..."
  if $VENV hyperliquid/run_all.py --paperdash-only; then
    if [ -f hyperliquid/output/json/papertrade_stats.json ]; then
      mkdir -p "$DEST/hyperliquid"
      cp -f hyperliquid/output/json/papertrade_stats.json "$DEST/hyperliquid/papertrade_stats.json" \
        || echo "[$(ts)]   [WARN] papertrade copy 失败"
    fi
  else
    echo "[$(ts)]   [WARN] paperdash 失败,继续"
  fi

  echo "[$(ts)] gmgn..."
  if $VENV gmgn/download.py; then
    _gmgn_push="${GMGN_FLASH_PUSH:-}"
    if [ -z "$_gmgn_push" ] && [ -f .env ]; then
      _gmgn_push="$(awk -F= '/^GMGN_FLASH_PUSH=/{v=$2} END{print v}' .env | tr -d '"' | tr -d "'")"
    fi
    if [ "${_gmgn_push:-1}" = "1" ]; then
      echo "[$(ts)] gmgn flash（过阈值才发）..."
      $VENV gmgn/flash.py --push || echo "[$(ts)]   [WARN] gmgn flash 失败,继续"
    fi
    unset _gmgn_push
    $VENV copy_to_dashboard.py --dest "$DEST" --only gmgn \
      || echo "[$(ts)]   [WARN] gmgn copy 失败"
  else
    echo "[$(ts)]   [WARN] gmgn 失败,继续"
  fi

  echo "[$(ts)] options(Deribit DVOL 增量)..."
  $VENV options/run_all.py || echo "[$(ts)]   [WARN] options 失败,继续"
  $VENV copy_to_dashboard.py --dest "$DEST" --only options \
    || echo "[$(ts)]   [WARN] options copy 失败"

  echo "[$(ts)] stablecoin(DefiLlama 增量)..."
  $VENV stablecoin/run_all.py || echo "[$(ts)]   [WARN] stablecoin 失败,继续"
  $VENV copy_to_dashboard.py --dest "$DEST" --only stablecoin \
    || echo "[$(ts)]   [WARN] stablecoin copy 失败"

  echo "[$(ts)] 完成"
} >> "$LOG" 2>&1
