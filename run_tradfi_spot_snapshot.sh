#!/bin/bash
# tradfi-spot 会话快照：Top 100 动态排名 + Yahoo quote + CEX ticker → tradfi-arb-live.json
# 建议 cron：每小时整点（美股 RTH 优先；休市可不跑）。
# 日志：tradfi-spot/_snapshot.log
set -o pipefail
cd /root/data-download || exit 1

VENV=./.venv/bin/python
LOG=/root/data-download/tradfi-spot/_snapshot.log
DEST=/root/dashboard/public/json
LOCK=/root/data-download/tradfi-spot/.locks/tradfi-spot.lock
ts() { date '+%Y-%m-%d %H:%M:%S'; }

mkdir -p /root/data-download/tradfi-spot/.locks
exec 9>"$LOCK"
flock -n 9 || { echo "[$(ts)] skip: spot lock busy" >> "$LOG"; exit 0; }

if ! $VENV _watermark.py --outbox /root/outbox >> "$LOG" 2>&1; then
  echo "[$(ts)] skip: watermark" >> "$LOG"; exit 0
fi

{
  echo "==================== [$(ts)] tradfi-spot snapshot 开始 ===================="
  safe-run timeout 180 $VENV tradfi-spot/snapshot_once.py --no-lock
  $VENV copy_to_dashboard.py --dest "$DEST" --only tradfi-spot-live --force \
    || echo "[$(ts)] copy live 失败(不影响本地产出)"
  echo "[$(ts)] 完成"
} >> "$LOG" 2>&1
