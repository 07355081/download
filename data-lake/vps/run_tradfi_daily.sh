#!/bin/bash
# tradfi 每日管道:下载 price(1d/4h/1h)+ OI + funding → 派生 parquet 进 /root/outbox
#                (供本机 rsync 拉走)→ 镜像 price json 到 dashboard(供网页,best-effort)。
# OI:历史型4家按interval拉,快照型6家每日1点累积;funding:9/10家有历史(Coinbase空)。
# 日志:tradfi/_daily.log。由 root crontab 每日 09:00 CST 调用。
#
# 本文件是 VPS 部署脚本的只读镜像(供本机版本控制/审查),真源在 VPS
# /root/data-download/run_tradfi_daily.sh。改动需要手动同步上去(SSH 登录后编辑或
# scp 覆盖),这里改了不会自动生效。
set -o pipefail
cd /root/data-download || exit 1

VENV=./.venv/bin/python
LOG=/root/data-download/tradfi/_daily.log
ts() { date '+%Y-%m-%d %H:%M:%S'; }

# flock 防重入:上一次没跑完就直接退出,避免并发下载
exec 9>/root/data-download/tradfi/.daily.lock
flock -n 9 || { echo "[$(ts)] 已有实例在运行,跳过本次" >> "$LOG"; exit 0; }

{
  echo "==================== [$(ts)] 每日 tradfi 开始 ===================="
  echo "[$(ts)] 1/3 下载 price(1d,4h,1h)+ OI + funding ..."
  $VENV tradfi/download.py --intervals 1d,4h,1h --workers 6
  echo "[$(ts)] 2/3 派生 parquet(price/oi/funding)→ /root/outbox ..."
  $VENV tradfi/to_parquet.py
  echo "[$(ts)] 3/3 镜像 price json → dashboard(供网页,best-effort) ..."
  $VENV copy_to_dashboard.py --dest /root/dashboard/public/json \
    || echo "[$(ts)] copy_to_dashboard 失败(不影响下载/parquet 产出)"
  echo "[$(ts)] 完成"
} >> "$LOG" 2>&1
