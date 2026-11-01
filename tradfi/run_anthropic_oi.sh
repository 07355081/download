#!/bin/bash
# Anthropic 多所 OI：周频日常 + 60 日倍率历史阈值 → Lark。
# 建议 crontab（排在 tradfi 日更 06:30 之后）：
# 40 7 * * * /usr/local/bin/safe-run /root/data-download/tradfi/run_anthropic_oi.sh
set -euo pipefail
cd /root/data-download
LOG="${LOG:-/var/log/dashboard-anthropic-oi.log}"
{
  echo "[$(date '+%F %T')] start"
  ./.venv/bin/python tradfi/anthropic_oi_flash.py --push
  echo "[$(date '+%F %T')] done"
} >>"$LOG" 2>&1
