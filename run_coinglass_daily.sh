#!/bin/bash
# coinglass-history 每日管道:增量下载 16 个模块(download.py + csv_to_json.py)
#   → 派生 parquet 进 /root/outbox(供本机 rsync --remove-source-files 拉走)。
# 增量口径:run_all.py 默认 recheck-days=2,只回补最近 2 天,单次产出小、outbox 不堆积。
# 首次全量历史回补请单独手动跑(见文件末尾说明),避免撑爆 VPS 根分区(~25G)。
# 日志:coinglass-history/_daily.log。由 root crontab 每日 01:00 CST 调用。
set -o pipefail
cd /root/data-download || exit 1

VENV=./.venv/bin/python
LOG=/root/data-download/coinglass-history/_daily.log
ts() { date '+%Y-%m-%d %H:%M:%S'; }

# 全局串行锁:三个每日管道共用同一把锁, 保证同一时刻只有一个重活在动盘。
# 本任务是当天第一棒, 耗时随数据量在长:2026-08 实测 5.5~6.3 小时(旧注释写的 3-4 小时已过时,
# 而下游 misc 的等锁死线正是按那个旧数字设的 2 小时, 导致 08-25 与 08-30 整条 misc 被跳过)。
# 改动耗时前先看 coinglass-history/_daily.log 里 1/2 那步的实际用时, 并同步下游两条管道的 -w。
# -w 7200 是排队等而不是跳过:宁可晚跑, 不丢当天数据。这里第一棒通常拿得到锁, 无需放宽。
exec 9>/root/data-download/.pipeline.lock
flock -w 7200 9 || { echo "[$(ts)] 等锁超时(另一个管道仍在跑),跳过本次" >> "$LOG"; exit 0; }

# 高水位预检:磁盘将满 / outbox 未被本机拉走则本轮直接跳过(下载器内部也有护栏兜底)
if ! $VENV _watermark.py --outbox /root/outbox >> "$LOG" 2>&1; then
  echo "[$(ts)] 触发高水位,跳过本轮 coinglass(详见上一行)" >> "$LOG"; exit 0
fi

{
  echo "==================== [$(ts)] 每日 coinglass-history 开始 ===================="
  echo "[$(ts)] 1/2 增量下载 16 模块(download + csv_to_json,recheck-days=2)..."
  $VENV coinglass-history/run_all.py --workers 4 --sleep 0.25 --recheck-days 2
  echo "[$(ts)] 2/2 派生 parquet(全部模块)→ /root/outbox ..."
  $VENV coinglass-history/to_parquet.py
  echo "[$(ts)] 完成"
} >> "$LOG" 2>&1

# 首次全量历史回补(一次性,手动执行,注意分区容量):
#   ./.venv/bin/python coinglass-history/run_all.py --workers 4 --sleep 0.25 --force
#   ./.venv/bin/python coinglass-history/to_parquet.py
# 建议在 disk-watermark-guard 护栏上线后再做,或分模块 --only 逐个跑并等本机拉空 outbox。
