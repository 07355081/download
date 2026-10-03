#!/usr/bin/env bash
# 定时从 VPS outbox 拉取 parquet 分块到本机 data-lake（拉走即删 VPS 源）。
#
# 本文件是版本控制的源头；实际由 Windows 计划任务「DataLake Pull Outbox」调用：
#   wsl.exe -d Ubuntu -u root -- bash /mnt/e/Code/data-download/data-lake/pull_outbox.sh
# 每 30 分钟一次，见同目录 register_pull_task.ps1（重跑该脚本即可重新注册/更新任务）。
#
# 契约（与 VPS 侧 tradfi/to_parquet.py 对应，见 data-lake/vps/to_parquet.py 镜像件）：
#   VPS /root/outbox/<module>/<EXCHANGE>/<EXCHANGE>_<SYMBOL>_<interval>.parquet
#   都是 os.replace() 原子改名产生的完整文件；.staging/ 是半成品，本脚本不碰。
#
# 特性：flock 并发锁 / 每日日志 / rsync 失败退避重试 / 拉后清 VPS 空目录。
#
# 运行时状态（锁文件、日志）故意放在 /mnt/e/data-pipeline（仓库外），避免日志把 git 仓库撑大。
set -uo pipefail

VPS=root@47.74.6.207
SRC=/root/outbox/
DST=/mnt/e/data-lake/
RUNTIME_DIR=/mnt/e/data-pipeline
LOCK="$RUNTIME_DIR/.pull.lock"
LOGDIR="$RUNTIME_DIR/logs"
MAX_TRIES=5
SSH_OPTS="ssh -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=10 -o ServerAliveCountMax=3"

mkdir -p "$LOGDIR"
LOG="$LOGDIR/pull_$(date +%Y%m%d).log"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >>"$LOG"; }

# 并发锁：上一轮未结束就直接跳过（避免 --remove-source-files 竞争/重复拉取）
exec 9>"$LOCK"
if ! flock -n 9; then
  log "SKIP: previous run still holding lock"
  exit 0
fi

log "=== pull start ==="
attempt=1
rc=1
while [ "$attempt" -le "$MAX_TRIES" ]; do
  rsync -a --remove-source-files --prune-empty-dirs --exclude='.staging/' \
    -e "$SSH_OPTS" "$VPS:$SRC" "$DST" >>"$LOG" 2>&1
  rc=$?
  # rc=0 成功；rc=24 源文件在传输前消失（VPS 端刚被清理），可容忍
  if [ "$rc" -eq 0 ] || [ "$rc" -eq 24 ]; then
    log "rsync OK (rc=$rc) on attempt $attempt"
    break
  fi
  log "rsync FAIL (rc=$rc) attempt $attempt/$MAX_TRIES; retry in $((attempt * 10))s"
  sleep $((attempt * 10))
  attempt=$((attempt + 1))
done

if [ "$rc" -ne 0 ] && [ "$rc" -ne 24 ]; then
  log "=== pull FAILED after $MAX_TRIES tries (rc=$rc) ==="
  exit "$rc"
fi

# 清 VPS 空目录（不动 .staging）
ssh -o BatchMode=yes -o ConnectTimeout=20 "$VPS" \
  "find /root/outbox -mindepth 1 -type d -empty -not -path '*/.staging*' -delete" >>"$LOG" 2>&1
log "cleaned vps empty dirs (rc=$?)"

pulled=$(find "${DST}tradfi-price" -name '*.parquet' 2>/dev/null | wc -l)
log "=== pull done; local parquet total now: $pulled ==="
exit 0
