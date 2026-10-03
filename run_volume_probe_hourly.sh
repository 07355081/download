#!/bin/bash
# CoinGecko volume_chart 定点历史小时采样（只读举证，不写 major_vol / dashboard）
# 2026-08-13 已停用 cron：结论已足够，生产沿用闭合月冻结；需复测时手动运行本脚本。
set -o pipefail
cd /root/data-download || exit 1

VENV=./.venv/bin/python
LOG=/root/data-download/cex-asset\&vol/cache/probes/probe_hourly.log
mkdir -p /root/data-download/cex-asset\&vol/cache/probes
ts() { date '+%Y-%m-%d %H:%M:%S'; }

# 与每日管道共用全局锁。这里用 -n 直接跳过而不是排队:只是定点取样,
# 补跑一个滞后的时间点没有意义, 反而会和重活抢盘。
exec 9>/root/data-download/.pipeline.lock
flock -n 9 || { echo "[$(ts)] 管道忙,跳过本次探针" >> "$LOG"; exit 0; }

{
  echo "----- [$(ts)] volume probe -----"
  $VENV "cex-asset&vol/probe_volume_stability.py" --sample \
    || echo "[$(ts)] [WARN] probe failed (ignored)"
} >> "$LOG" 2>&1
