#!/usr/bin/env bash
set -euo pipefail

export MSYS2_ARG_CONV_EXCL='*'

SRC="/c/code/data-dashboard/public/json/"
DST="root@47.74.6.207:/root/dashboard/public/json/"
SSH_OPTS="-i /c/Users/Joeyw/.ssh/data-dashboard-prod -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -o TCPKeepAlive=yes"
LOG="/tmp/rsync_resume.log"

: > "$LOG"

n=0
while true; do
  if rsync -rlz --size-only --delete --partial --timeout=120 \
      --info=progress2 --stats \
      -e "ssh $SSH_OPTS" \
      "$SRC" "$DST" >> "$LOG" 2>&1; then
    echo "RSYNC_DONE_OK" >> "$LOG"
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
