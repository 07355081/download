#!/usr/bin/env bash
# 反向拉取: 从 VPS 把 dashboard 的 public/json 增量同步回本机(供本地 next dev 预览最新数据)。
# 【在本机(Windows Git-Bash / MSYS2)执行, 不是在 VPS 执行】
#
# 设计要点(谨慎保护 VPS 资源):
#   - 只增量新增/更新, 【绝不加 --delete】: 不会删本机任何文件, 也不会动本机独有模块。
#   - --size-only: 仅按大小判断是否传输, 不做逐字节 checksum, VPS 侧 CPU 几乎零负担。
#   - --bwlimit=400 (≈3.2Mbit/s): 客户端软限速, 叠加 VPS 上 tc 的 4.5Mbps 全局硬顶,
#     双保险确保永不打满 5Mbps 公网出带宽、不抢占线上站点/SSH。
#   - --exclude: 排除 VPS 上暂缓/空/旧的模块(本机是权威版本), 防被覆盖。
#   - 断点续传 + 超时重试, 弱网也能最终拉完。
set -euo pipefail
export MSYS2_ARG_CONV_EXCL='*'

SRC="root@47.74.6.207:/root/dashboard/public/json/"
DST="/c/code/data-dashboard/public/json/"
SSH_OPTS="-i /c/Users/Joeyw/.ssh/data-dashboard-prod -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -o TCPKeepAlive=yes"
LOG="/tmp/rsync_from_vps.log"

# VPS 上暂缓/未搬迁/空的模块 -> 本机保留自己的权威版本, 不从 VPS 拉(避免旧数据覆盖)。
# mining-shutdown-price 已迁 VPS(mempool+Bitmain+2Miners),不再 exclude。
EXCLUDES=(
  "--exclude=cex-asset-vol/"
  "--exclude=tag/"
)

: > "$LOG"
n=0
while true; do
  if rsync -rlz --size-only --partial --timeout=120 --bwlimit=400 \
      "${EXCLUDES[@]}" \
      --info=progress2 --stats \
      -e "ssh $SSH_OPTS" \
      "$SRC" "$DST" >> "$LOG" 2>&1; then
    echo "RSYNC_FROM_VPS_DONE_OK" >> "$LOG"
    exit 0
  fi
  n=$((n + 1))
  echo "=== RETRY $n at $(date) ===" >> "$LOG"
  if [ "$n" -ge 60 ]; then
    echo "RSYNC_GAVE_UP" >> "$LOG"
    exit 1
  fi
  sleep 15
done
