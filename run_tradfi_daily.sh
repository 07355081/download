#!/bin/bash
# tradfi 每日管道:下载 price(1d/4h/1h)+ OI + funding → 派生 parquet 进 /root/outbox
#                (供本机 rsync 拉走)→ 镜像 price json 到 dashboard(供网页,best-effort)。
# OI:历史型4家按interval拉,快照型6家每日1点累积;funding:9/10家有历史(Coinbase空)。
# 日志:tradfi/_daily.log。由 root crontab 每日 06:30 CST 调用(当天最后一个管道)。
set -o pipefail
cd /root/data-download || exit 1

VENV=./.venv/bin/python
LOG=/root/data-download/tradfi/_daily.log
ts() { date '+%Y-%m-%d %H:%M:%S'; }

# 全局串行锁:三个每日管道共用同一把锁, 保证同一时刻只有一个重活在动盘。
# 等锁上限要覆盖 coinglass(01:00 起, 5.5~6.3h) + misc(约 10min) 两条前置管道排完队。
# 原来的 2 小时(死线 08:30)在 coinglass 慢到 07:18 那天就不够了 —— 而这一步还负责刷
# _manifest.json 和推新鲜度告警, 它被跳过就等于当天所有数据集的监控一起失效。
exec 9>/root/data-download/.pipeline.lock
flock -w 21600 9 || { echo "[$(ts)] 等锁超时(另一个管道仍在跑),跳过本次" >> "$LOG"; exit 0; }

# 高水位预检:磁盘将满 / outbox 未被本机拉走则本轮直接跳过(下载器内部也有护栏兜底)
if ! $VENV _watermark.py --outbox /root/outbox >> "$LOG" 2>&1; then
  echo "[$(ts)] 触发高水位,跳过本轮 tradfi(详见上一行)" >> "$LOG"; exit 0
fi

{
  echo "==================== [$(ts)] 每日 tradfi 开始 ===================="
  echo "[$(ts)] 1/5 下载 price(1d,4h,1h)+ OI + funding ..."
  $VENV tradfi/download.py --intervals 1d,4h,1h --workers 6
  echo "[$(ts)] 2/5 派生 parquet(price/oi/funding)→ /root/outbox ..."
  $VENV tradfi/to_parquet.py
  echo "[$(ts)] 3/5 镜像 price json → dashboard(供网页,best-effort) ..."
  $VENV copy_to_dashboard.py --dest /root/dashboard/public/json \
    || echo "[$(ts)] copy_to_dashboard 失败(不影响下载/parquet 产出)"
  echo "[$(ts)] 4/5 刷新目录索引与数据契约(_indexes/ + _manifest.json)..."
  $VENV gen_dashboard_meta.py --dest /root/dashboard/public/json \
    || echo "[$(ts)] gen_dashboard_meta 失败(不影响数据本身)"
  # 新鲜度闸门:当天最后一个管道收尾时按数据集阈值检查 manifest, 超阈值推飞书。
  # 不接这一步, 单个数据集静默停更上百天也不会有人发现(cex 停 10 天/treasuries 停 45 天
  # /etf-premium 停 105 天都是这么积累出来的)。已知长期停更项在脚本内单列阈值避免刷屏。
  echo "[$(ts)] 5/5 检查数据新鲜度(超阈值推飞书)..."
  $VENV check_manifest_freshness.py \
    || echo "[$(ts)] check_manifest_freshness 失败(不影响数据本身)"
  echo "[$(ts)] 完成"
} >> "$LOG" 2>&1
