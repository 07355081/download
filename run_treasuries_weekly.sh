#!/bin/bash
# crypto-treasuries 每周全量刷新。由 root crontab 每周日 09:00 调用。
#
# 为什么需要这一条:
#   日常 misc 管道用 --mode web, 只精修 top 实体的 chart/tx, 其余实体全靠 cache
#   保留。而 1.download.py 的缓存逻辑是 `cache.exists() and not force` 即跳过,
#   所以不带 --force 时实体级数据会永久停在上一次 full 的日期。实测曾停在
#   2026-07-15 长达 45 天, 而 _manifest.json 的 last_day 取所有子文件最大值,
#   刚好被 overview(每日更新)掩盖, 因此长期无人发现。
#
# 为什么限速参数这么保守:
#   1.download.py 的 resolve_key() 带硬编码 demo key 兜底, 脚本因此误判为
#   "有正式 key" 并启用 batch=4 并发 + sleep=1.5(约 160 req/min), 而 demo key
#   实际只有约 30 req/min。实测默认参数下 429 占日志八成、backoff 升到 60s 且
#   一小时都跑不完第一个币种。下面的参数把速率压到约 24 req/min。
#
# 实测成本:约 1460 个请求(chart 1004 + tx 251 + detail 184 + overview/catalog),
#   按 24 req/min 约需 65 分钟。周日 09:00 时段无其他 cron, 且与三个日更管道
#   共用 .pipeline.lock 串行, 不会与它们争 CoinGecko 配额。
set -o pipefail
cd /root/data-download || exit 1

VENV=./.venv/bin/python
LOG=/root/data-download/crypto-treasuries/_weekly_full.log
JSON_DIR=crypto-treasuries/output/json
CACHE_DIR=crypto-treasuries/cache

# 产出健全性下限。基线 962 个 json / 1478 个 cache 文件。
MIN_JSON_FILES=900
MIN_FRESH_CACHE=400

ts() { date '+%Y-%m-%d %H:%M:%S'; }

exec 9>/root/data-download/.pipeline.lock
flock -w 3600 9 || {
  echo "[$(ts)] 等锁超时(另一个管道仍在跑),跳过本次" >> "$LOG"
  exit 0
}

{
  echo "==================== [$(ts)] treasuries 每周全量开始 ===================="

  echo "[$(ts)] 1/4 全量下载(--mode full --force, 限速 ~24 req/min, 预计约 65 分钟)..."
  $VENV crypto-treasuries/run_all.py --mode full --force \
    --enrich-batch-size 1 --enrich-batch-delay-ms 2500 --sleep 2.5 --coin-delay-s 5 \
    || echo "[$(ts)]   [WARN] 全量下载返回非零,仍继续做产出校验"

  # 镜像前的闸门:全量会重写近千个文件, 上游大面积失败时不能把残缺产出推上线。
  JSON_COUNT=$(ls "$JSON_DIR" 2>/dev/null | wc -l)
  # 不能写 -newermt 'today':GNU date 把 today 解析成"当前时刻"而非今天 00:00,
  # 那样这个计数恒为 0, 闸门会永远拦下镜像、全量白跑(已实测踩过)。
  # 用 -mmin 覆盖整个运行时长(实测约 65 分钟)并留足余量。
  FRESH_CACHE=$(find "$CACHE_DIR" -type f -mmin -240 2>/dev/null | wc -l)
  echo "[$(ts)] 2/4 产出校验: json=$JSON_COUNT(下限 $MIN_JSON_FILES) 近 4h 刷新 cache=$FRESH_CACHE(下限 $MIN_FRESH_CACHE)"

  if [ "$JSON_COUNT" -lt "$MIN_JSON_FILES" ]; then
    echo "[$(ts)]   [ABORT] json 文件数不足,跳过镜像。cache/output 保持原样,等下周或人工介入"
  elif [ "$FRESH_CACHE" -lt "$MIN_FRESH_CACHE" ]; then
    echo "[$(ts)]   [ABORT] 本次刷新的 cache 过少,说明全量基本没成功,跳过镜像"
  else
    echo "[$(ts)] 3/4 镜像 json → dashboard ..."
    $VENV copy_to_dashboard.py --dest /root/dashboard/public/json --only treasuries \
      || echo "[$(ts)]   [WARN] copy_to_dashboard 失败"

    # 顺手刷 manifest, 否则新鲜度要等到周一 07:15 的 tradfi 管道才更新,
    # 新鲜度告警在周日当天会仍按旧 last_day 判定。
    echo "[$(ts)] 4/4 刷新目录索引与数据契约..."
    $VENV gen_dashboard_meta.py --dest /root/dashboard/public/json \
      || echo "[$(ts)]   [WARN] gen_dashboard_meta 失败(不影响数据本身)"
  fi

  echo "[$(ts)] 完成"
} >> "$LOG" 2>&1
