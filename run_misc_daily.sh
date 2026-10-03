#!/bin/bash
# 杂项模块每日管道(非 coinglass/tradfi):
#   btc-index(仅 Coinglass)/ crypto-treasuries(web 轻量)/ options(Deribit DVOL)/ stablecoin(DefiLlama)
#   / hyperliquid(Dune 小表缓存超过 36h 才执行;按币种走官方接口)
#   / uni-burn(Dune 窄查询,缓存超过 20h 才执行)
#   / robinhood-chain(Dune 7 条 query:日交易笔数/活跃地址/DEX 成交/Launchpad/RWA 市值/币股成交;
#                     多数读缓存;launchpad 窄查询单独 72h)
#   / mining-shutdown-price(Bitmain 实时 + mempool BTC 历史 + 2Miners;跳过 BitInfoCharts)
#   / cex-asset&vol(CoinGecko 成交量 + DefiLlama USD/BTC 储备;中间产物在 cache/,发布 output/json)
# 流程:增量下载 → csv_to_json(json 留 output/json)→ 派生 parquet 进 /root/outbox(供本机 rsync 拉走)
#       → copy_to_dashboard 逐模块镜像 json 到 dashboard(供网页,mirror-delete 仅作用于各自目录)。
# 目录索引/数据契约(_indexes/ + _manifest.json)不在这里刷,由当天最后的 tradfi 管道统一收尾。
# 增量口径:btc-index recheck-days=2;treasuries --mode web(只精修 top 实体,其余靠 cache 保留);
#           options 靠 output/csv 下的现存 csv 算增量起点, 只清带日期的 json;stablecoin DefiLlama 增量;
#           mining-shutdown-price BTC 链指标走 mempool.space(全量重拉,体量小);
#           cex-asset&vol vol-mode=append(补 major_vol 空洞)+DefiLlama 资产/BTC 增量。
# 日志:_misc_daily.log。由 root crontab 每日 05:00 调用(coinglass 01:00 / tradfi 06:30,共用全局锁串行)。
# 顺序不能乱:tradfi 要排在 misc 之后, 因为它收尾时才刷 _manifest.json 与新鲜度告警,
# 提前跑会把 misc 这一轮的产出漏在契约之外, 让告警误报。
set -o pipefail
cd /root/data-download || exit 1

VENV=./.venv/bin/python
LOG=/root/data-download/_misc_daily.log
ts() { date '+%Y-%m-%d %H:%M:%S'; }

# 全局串行锁:三个每日管道共用同一把锁, 保证同一时刻只有一个重活在动盘。
# 等锁上限必须 > coinglass 的实际耗时:它 01:00 起跑, 近期要 5.5~6.3 小时(2026-08-30 跑到 07:18),
# 原来给 2 小时(死线 07:00)导致 08-25 与 08-30 整条 misc 被静默跳过 —— 12 个模块一起停更一天。
# 现在给 6 小时(死线 11:00), 能容忍 coinglass 慢到 10:00。flock 是阻塞等待, 空等不吃资源。
exec 9>/root/data-download/.pipeline.lock
flock -w 21600 9 || { echo "[$(ts)] 等锁超时(另一个管道仍在跑),跳过本次" >> "$LOG"; exit 0; }

# 高水位预检:磁盘将满 / outbox 未被本机拉走则本轮直接跳过
if ! $VENV _watermark.py --outbox /root/outbox >> "$LOG" 2>&1; then
  echo "[$(ts)] 触发高水位,跳过本轮 misc(详见上一行)" >> "$LOG"; exit 0
fi

{
  echo "==================== [$(ts)] 每日 misc 开始 ===================="

  echo "[$(ts)] 1/12 btc-index(仅 Coinglass,recheck-days=2)..."
  $VENV btc-index/run_all.py --recheck-days 2 || echo "[$(ts)]   [WARN] btc-index 失败,继续"

  # web 模式只精修 top 实体, 且 1.download.py 的 cache 命中即跳过(除 --force),
  # 所以实体级 chart/tx 不会在这里更新。全量由 run_treasuries_weekly.sh 每周日 09:00 补。
  echo "[$(ts)] 2/12 crypto-treasuries(--mode web 轻量增量;实体级全量见每周任务)..."
  $VENV crypto-treasuries/run_all.py --mode web || echo "[$(ts)]   [WARN] treasuries 失败,继续"

  echo "[$(ts)] 3/12 options(Deribit DVOL 增量)..."
  $VENV options/run_all.py || echo "[$(ts)]   [WARN] options 失败,继续"
  # 只删带日期的 json(单个 4MB, 前端只读 *_latest_*)。
  # csv 不能删:download.py 靠现存 csv 末尾的时间戳算增量起点, 删掉就等于每天从
  # 2021-03-23 全量重下(约 96 请求 x 2 币种)。它写完新 csv 会自己 unlink 旧的, 不会堆积。
  find options/output/json -name 'dvol_*.json' ! -name '*_latest_*' -delete 2>/dev/null

  echo "[$(ts)] 4/12 stablecoin(DefiLlama 增量)..."
  $VENV stablecoin/run_all.py || echo "[$(ts)]   [WARN] stablecoin 失败,继续"

  # 必须排在 stablecoin 之后:SSR 的分母就是上一步产出的稳定币总市值。
  # glassnode 会员到期后 001_mvrv-z-score 停在 2026-06-15、066_ssr 停在 2026-03-21,
  # 这一步用免费源(BGeometrics / CoinMetrics community)把两个指标重建出来。
  echo "[$(ts)] 5/12 btc-index 派生指标(MVRV Z / SSR,免费源重建)..."
  $VENV btc-index/download_derived.py || echo "[$(ts)]   [WARN] download_derived 失败,继续"

  echo "[$(ts)] 6/12 hyperliquid(Dune 小表缓存超过 36h 才执行;按币种走官方接口)..."
  $VENV hyperliquid/run_all.py || echo "[$(ts)]   [WARN] hyperliquid 失败,继续"

  echo "[$(ts)] 7/12 uni-burn(Dune 窄查询,缓存超过 20h 才执行)..."
  $VENV uni-burn/run_all.py || echo "[$(ts)]   [WARN] uni-burn 失败,继续"

  echo "[$(ts)] 8/12 robinhood-chain(Dune 7 条 query,缓存超 48h 才自己触发执行)..."
  $VENV robinhood-chain/run_all.py || echo "[$(ts)]   [WARN] robinhood-chain 失败,继续"

  echo "[$(ts)] 9/12 mining-shutdown-price(Bitmain+mempool+2Miners,跳过 BitInfoCharts)..."
  $VENV mining-shutdown-price/run_all.py --skip-bitinfocharts \
    || echo "[$(ts)]   [WARN] mining-shutdown-price 失败,继续"

  echo "[$(ts)] 10/12 cex-asset&vol(CoinGecko vol + DefiLlama assets/BTC)..."
  $VENV "cex-asset&vol/run_all.py" --vol-mode append \
    || echo "[$(ts)]   [WARN] cex-asset&vol 失败,继续"

  echo "[$(ts)] 11/12 派生 parquet → /root/outbox ..."
  $VENV to_parquet_misc.py --modules btc-index crypto-treasuries options stablecoin mining-shutdown-price \
    || echo "[$(ts)]   [WARN] to_parquet_misc 失败,继续"

  echo "[$(ts)] 12/12 镜像 json → dashboard(供网页) ..."
  for only in btc-index treasuries options stablecoin hyperliquid uni-burn robinhood-chain mining-shutdown-price cex; do
    $VENV copy_to_dashboard.py --dest /root/dashboard/public/json --only "$only" \
      || echo "[$(ts)]   [WARN] copy_to_dashboard --only $only 失败"
  done

  # gen_dashboard_meta 要全量遍历 dashboard 下 7.5 万个 json, 一天跑两遍纯属浪费 IO。
  # 统一交给当天最后一个管道 run_tradfi_daily.sh 收尾时跑一次。
  echo "[$(ts)] 完成"
} >> "$LOG" 2>&1

# 首次全量历史回补(一次性,手动;treasuries 用 full 建全实体 cache):
# 注意 treasuries 并不是"建完 cache 之后日常 web 即可"——web 模式不刷实体级 cache,
# 必须靠 run_treasuries_weekly.sh 定期全量, 否则会像 2026-07-15 到 08-29 那样停更 45 天,
# 且因 _manifest.json 的 last_day 取子文件最大值而被每日更新的 overview 掩盖。
#   ./.venv/bin/python run_all.py --only btc-index,crypto-treasuries,options,stablecoin
#   ./.venv/bin/python mining-shutdown-price/run_all.py --skip-bitinfocharts
#   ./.venv/bin/python "cex-asset&vol/run_all.py"
#   ./.venv/bin/python to_parquet_misc.py
#   ./.venv/bin/python copy_to_dashboard.py --dest /root/dashboard/public/json --only all
