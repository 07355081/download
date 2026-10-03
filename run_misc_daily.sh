#!/bin/bash
# 杂项模块每日管道(非 coinglass/tradfi):
#   btc-index(仅 Coinglass)/ crypto-treasuries(web 轻量)/ options(Deribit DVOL)/ stablecoin(DefiLlama)
#   / hyperliquid(Dune Analytics,读 ASXN 查询缓存结果)
#   / uni-burn(Dune Analytics query 8260046,读 UNI 按链销毁缓存结果)
#   / mining-shutdown-price(Bitmain 实时 + mempool BTC 历史 + 2Miners;跳过 BitInfoCharts)
#   / cex-asset&vol(CoinGecko 成交量 + DefiLlama USD/BTC 储备;中间产物在 cache/,发布 output/json)
# 流程:增量下载 → csv_to_json(json 留 output/json)→ 派生 parquet 进 /root/outbox(供本机 rsync 拉走)
#       → copy_to_dashboard 逐模块镜像 json 到 dashboard(供网页,mirror-delete 仅作用于各自目录)。
# 目录索引/数据契约(_indexes/ + _manifest.json)不在这里刷,由当天最后的 tradfi 管道统一收尾。
# 增量口径:btc-index recheck-days=2;treasuries --mode web(只精修 top 实体,其余靠 cache 保留);
#           options 全量刷新后只留 *_latest_*;stablecoin DefiLlama 增量;
#           mining-shutdown-price BTC 链指标走 mempool.space(全量重拉,体量小);
#           cex-asset&vol vol-mode=append(补 major_vol 空洞)+DefiLlama 资产/BTC 增量。
# 日志:_misc_daily.log。由 root crontab 每日 05:00 调用(coinglass 01:00 / tradfi 06:30,共用全局锁串行)。
# 等锁要盖住 coinglass 的实际耗时(近期 5.5~6.3 小时)。2 小时死线会在 coinglass 跑过 07:00 时把整条 misc 静默跳过。
set -o pipefail
cd /root/data-download || exit 1

VENV=./.venv/bin/python
LOG=/root/data-download/_misc_daily.log
ts() { date '+%Y-%m-%d %H:%M:%S'; }

# 全局串行锁:三个每日管道共用同一把锁, 保证同一时刻只有一个重活在动盘。
exec 9>/root/data-download/.pipeline.lock
flock -w 21600 9 || { echo "[$(ts)] 等锁超时(另一个管道仍在跑),跳过本次" >> "$LOG"; exit 0; }

# 高水位预检:磁盘将满 / outbox 未被本机拉走则本轮直接跳过
if ! $VENV _watermark.py --outbox /root/outbox >> "$LOG" 2>&1; then
  echo "[$(ts)] 触发高水位,跳过本轮 misc(详见上一行)" >> "$LOG"; exit 0
fi

{
  echo "==================== [$(ts)] 每日 misc 开始 ===================="

  echo "[$(ts)] 1/10 btc-index(仅 Coinglass,recheck-days=2)..."
  $VENV btc-index/run_all.py --recheck-days 2 || echo "[$(ts)]   [WARN] btc-index 失败,继续"

  echo "[$(ts)] 2/10 crypto-treasuries(--mode web 轻量增量)..."
  $VENV crypto-treasuries/run_all.py --mode web || echo "[$(ts)]   [WARN] treasuries 失败,继续"

  echo "[$(ts)] 3/10 options(Deribit DVOL 刷新)..."
  $VENV options/run_all.py || echo "[$(ts)]   [WARN] options 失败,继续"
  # 前端只读 dvol_*_latest_*;删带日期归档 json/csv,避免逐日堆积
  find options/output/json -name 'dvol_*.json' ! -name '*_latest_*' -delete 2>/dev/null
  find options/output/csv  -name 'dvol_*.csv'  ! -name '*_latest_*' -delete 2>/dev/null

  echo "[$(ts)] 4/10 stablecoin(DefiLlama 增量)..."
  $VENV stablecoin/run_all.py || echo "[$(ts)]   [WARN] stablecoin 失败,继续"

  # 必须排在 stablecoin 之后:SSR 的分母就是上一步产出的稳定币总市值。
  echo "[$(ts)] btc-index 派生指标(MVRV Z / SSR,免费源重建)..."
  $VENV btc-index/download_derived.py || echo "[$(ts)]   [WARN] download_derived 失败,继续"

  echo "[$(ts)] 5/10 hyperliquid(Dune get_latest_result,读缓存结果不重跑)..."
  $VENV hyperliquid/run_all.py || echo "[$(ts)]   [WARN] hyperliquid 失败,继续"

  echo "[$(ts)] 6/10 uni-burn(Dune get_latest_result query 8260046,读缓存结果不重跑)..."
  $VENV uni-burn/run_all.py || echo "[$(ts)]   [WARN] uni-burn 失败,继续"

  echo "[$(ts)] 7/10 mining-shutdown-price(Bitmain+mempool+2Miners,跳过 BitInfoCharts)..."
  $VENV mining-shutdown-price/run_all.py --skip-bitinfocharts \
    || echo "[$(ts)]   [WARN] mining-shutdown-price 失败,继续"

  echo "[$(ts)] 8/10 cex-asset&vol(CoinGecko vol + DefiLlama assets/BTC)..."
  $VENV "cex-asset&vol/run_all.py" --vol-mode append \
    || echo "[$(ts)]   [WARN] cex-asset&vol 失败,继续"

  echo "[$(ts)] 9/10 派生 parquet → /root/outbox ..."
  $VENV to_parquet_misc.py --modules btc-index crypto-treasuries options stablecoin mining-shutdown-price \
    || echo "[$(ts)]   [WARN] to_parquet_misc 失败,继续"

  echo "[$(ts)] 10/10 镜像 json → dashboard(供网页) ..."
  for only in btc-index treasuries options stablecoin hyperliquid uni-burn mining-shutdown-price cex; do
    $VENV copy_to_dashboard.py --dest /root/dashboard/public/json --only "$only" \
      || echo "[$(ts)]   [WARN] copy_to_dashboard --only $only 失败"
  done

  # gen_dashboard_meta 要全量遍历 dashboard 下 7.5 万个 json, 一天跑两遍纯属浪费 IO。
  # 统一交给当天最后一个管道 run_tradfi_daily.sh 收尾时跑一次。
  echo "[$(ts)] 完成"
} >> "$LOG" 2>&1

# 首次全量历史回补(一次性,手动;treasuries 用 full 建全实体 cache,之后日常 web 即可):
#   ./.venv/bin/python run_all.py --only btc-index,crypto-treasuries,options,stablecoin
#   ./.venv/bin/python mining-shutdown-price/run_all.py --skip-bitinfocharts
#   ./.venv/bin/python "cex-asset&vol/run_all.py"
#   ./.venv/bin/python to_parquet_misc.py
#   ./.venv/bin/python copy_to_dashboard.py --dest /root/dashboard/public/json --only all
