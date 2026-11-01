#!/bin/bash
# 杂项模块每日管道(非 coinglass/tradfi):
#   btc-index(仅 Coinglass)/ crypto-treasuries(web 轻量)/ options(Deribit DVOL)/ stablecoin(DefiLlama)
#   / hyperliquid(Dune 步日常 --no-execute;按币种/PaperDash/fees 走官方或 DefiLlama)
#   / uni-burn(Dune 窄查询,缓存超过 20h 才执行)
#   / robinhood-chain(自有窄查询超 20h 执行;贵查询 7~14 天才自跑)
#   / gmgn(OpenAPI 多链 Meme 24h volume TopN;需 IPv4 出网;可选 GMGN_FLASH_PUSH=1 推飞书)
#   / perp-dex-oi(DefiLlama open-interest 衍生品一类;日常+阈值 Lark;不回扫旧日期)
#   / scroll-l2(DefiLlama Scroll 锁仓+链收入;日常+新低 Lark;不回扫旧日期)
#   / arc-app-fees(DefiLlama Arc+对照链应用手续费近似;日常+阈值 Lark;非网络 gas)
#   / protocol-revenue-ytd(DefiLlama 观察名单 YTD 收入榜近似;日常+阈值 Lark;非 CoinGecko API)
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

  # 分链市值日常 / 阈值（DefiLlama 稳定币口径；失败不阻断 misc）
  echo "[$(ts)] stablecoin chain-mcap flash → Lark..."
  $VENV stablecoin/chain_mcap_flash.py --push --history-scan \
    || echo "[$(ts)]   [WARN] chain-mcap flash 失败,继续"

  echo "[$(ts)] usdt-eth-rank(CoinGecko USDT 市值超过 ETH，仅穿越)..."
  if $VENV usdt-eth-rank/download.py; then
    $VENV usdt-eth-rank/flash.py --push \
      || echo "[$(ts)]   [WARN] usdt-eth-rank flash 失败,继续"
    mkdir -p /root/dashboard/public/json/usdt-eth-rank
    cp -a usdt-eth-rank/output/json/usdt_eth_rank.json \
      /root/dashboard/public/json/usdt-eth-rank/usdt_eth_rank.json \
      || echo "[$(ts)]   [WARN] usdt-eth-rank json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] usdt-eth-rank 失败,继续"
  fi

  echo "[$(ts)] rlusd-chain(DefiLlama RLUSD ETH/XRPL 流通占比,无转账/无 Dune)..."
  if $VENV rlusd-chain/download.py; then
    echo "[$(ts)] rlusd-chain flash → Lark..."
    $VENV rlusd-chain/flash.py --push \
      || echo "[$(ts)]   [WARN] rlusd-chain flash 失败,继续"
    mkdir -p /root/dashboard/public/json/rlusd-chain
    cp -a rlusd-chain/output/json/rlusd_chain.json \
      /root/dashboard/public/json/rlusd-chain/rlusd_chain.json \
      || echo "[$(ts)]   [WARN] rlusd-chain json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] rlusd-chain 失败,继续"
  fi

  echo "[$(ts)] binance-tron-usdt(DefiLlama Binance CEX · Tron USDT,无 Dune)..."
  if $VENV binance-tron-usdt/download.py; then
    $VENV binance-tron-usdt/flash.py --push \
      || echo "[$(ts)]   [WARN] binance-tron-usdt flash 失败,继续"
    mkdir -p /root/dashboard/public/json/binance-tron-usdt
    cp -a binance-tron-usdt/output/json/binance_tron_usdt.json \
      /root/dashboard/public/json/binance-tron-usdt/binance_tron_usdt.json \
      || echo "[$(ts)]   [WARN] binance-tron-usdt json 镜像失败,继续"
    cp -a binance-tron-usdt/chart.html \
      /root/dashboard/public/json/binance-tron-usdt/chart.html \
      || echo "[$(ts)]   [WARN] binance-tron-usdt chart 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] binance-tron-usdt 失败,继续"
  fi

  # 必须排在 stablecoin 之后:SSR 的分母就是上一步产出的稳定币总市值。
  # glassnode 会员到期后 001_mvrv-z-score 停在 2026-06-15、066_ssr 停在 2026-03-21,
  # 这一步用免费源(BGeometrics / CoinMetrics community)把两个指标重建出来。
  echo "[$(ts)] 5/12 btc-index 派生指标(MVRV Z / SSR,免费源重建)..."
  $VENV btc-index/download_derived.py || echo "[$(ts)]   [WARN] download_derived 失败,继续"

  echo "[$(ts)] 6/12 hyperliquid(Dune --no-execute;按币种/PaperDash/DefiLlama fees)..."
  $VENV hyperliquid/run_all.py -- --no-execute || echo "[$(ts)]   [WARN] hyperliquid 失败,继续"
  echo "[$(ts)] hyperliquid revenue flash → Lark..."
  $VENV hyperliquid/hl_revenue_flash.py --push \
    || echo "[$(ts)]   [WARN] hl revenue flash 失败,继续"
  echo "[$(ts)] tradexyz flash → Lark..."
  $VENV hyperliquid/tradexyz_flash.py --push --history-scan \
    || echo "[$(ts)]   [WARN] tradexyz flash 失败,继续"
  echo "[$(ts)] hip3-oi-share flash → Lark..."
  $VENV hyperliquid/hip3_oi_share_flash.py --push \
    || echo "[$(ts)]   [WARN] hip3-oi-share flash 失败,继续"
  echo "[$(ts)] hl-quarter-rwa flash → Lark（仅最新完整季，45 天内）..."
  $VENV hyperliquid/hl_quarter_rwa_flash.py --push \
    || echo "[$(ts)]   [WARN] hl-quarter-rwa flash 失败,继续"
  echo "[$(ts)] tradexyz-oi flash → Lark（仅持仓 ATH 新闻体）..."
  $VENV hyperliquid/tradexyz_oi_flash.py --push \
    || echo "[$(ts)]   [WARN] tradexyz-oi flash 失败,继续"
  echo "[$(ts)] spcx volume flash → Lark（仅 SPCX 日成交新高）..."
  $VENV hyperliquid/spcx_volume_flash.py --push \
    || echo "[$(ts)]   [WARN] spcx volume flash 失败,继续"
  mkdir -p /root/dashboard/public/json/hyperliquid
  cp -a hyperliquid/output/json/hip3_oi_share.json /root/dashboard/public/json/hyperliquid/hip3_oi_share.json \
    2>/dev/null || echo "[$(ts)]   [WARN] hip3_oi_share json 镜像失败,继续"
  cp -a hyperliquid/output/json/tradexyz_oi.json /root/dashboard/public/json/hyperliquid/tradexyz_oi.json \
    2>/dev/null || echo "[$(ts)]   [WARN] tradexyz_oi json 镜像失败,继续"

  echo "[$(ts)] cb-stock-dex(Dune 8958296, Base 上 Coinbase 股票代币)..."
  if $VENV cb-stock-dex/download.py; then
    echo "[$(ts)] cb-stock-dex flash → Lark..."
    $VENV cb-stock-dex/flash.py --push --history-scan \
      || echo "[$(ts)]   [WARN] cb-stock-dex flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] cb-stock-dex 失败,继续"
  fi

  echo "[$(ts)] dex-fees(DefiLlama Dexs+Launchpad 月度手续费,无 Dune)..."
  $VENV dex-fees/download.py || echo "[$(ts)]   [WARN] dex-fees 失败,继续"

  echo "[$(ts)] uniswap-volume(DefiLlama Uniswap v2/v3/v4 日成交量,无 Dune)..."
  $VENV uniswap-volume/download.py || echo "[$(ts)]   [WARN] uniswap-volume 失败,继续"

  echo "[$(ts)] uniswap-revenue(DefiLlama Uniswap 协议收入按链/RH 占比,无 Dune)..."
  if $VENV uniswap-revenue/download.py; then
    echo "[$(ts)] uniswap-revenue flash → Lark..."
    $VENV uniswap-revenue/flash.py --push --history-scan \
      || echo "[$(ts)]   [WARN] uniswap-revenue flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] uniswap-revenue 失败,继续"
  fi

  echo "[$(ts)] chain-fees-dist(DefiLlama 公链 24h 手续费长尾分档,无 Dune)..."
  if $VENV chain-fees-dist/download.py; then
    echo "[$(ts)] chain-fees-dist flash → Lark..."
    $VENV chain-fees-dist/flash.py --push --history-scan \
      || echo "[$(ts)]   [WARN] chain-fees-dist flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] chain-fees-dist 失败,继续"
  fi

  echo "[$(ts)] chain-app-revenue(DefiLlama 各链 24h 应用收入,非 gas)..."
  if $VENV chain-app-revenue/download.py; then
    echo "[$(ts)] chain-app-revenue flash → Lark..."
    $VENV chain-app-revenue/flash.py --push --history-scan \
      || echo "[$(ts)]   [WARN] chain-app-revenue flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] chain-app-revenue 失败,继续"
  fi

  echo "[$(ts)] hl-net-revenue(DefiLlama 近30日净收入，只推最新一条)..."
  if $VENV hl-net-revenue/download.py; then
    mkdir -p /root/dashboard/public/json/hl-net-revenue
    cp -a hl-net-revenue/output/json/hl_net_revenue.json \
      /root/dashboard/public/json/hl-net-revenue/hl_net_revenue.json \
      || echo "[$(ts)]   [WARN] hl-net-revenue json 镜像失败,继续"
    $VENV hl-net-revenue/flash.py --push \
      || echo "[$(ts)]   [WARN] hl-net-revenue flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] hl-net-revenue 失败,继续"
  fi

  # 近似 Glassnode「自 BTC ATH 前50跑赢」：今日名单 + ATH 日起相对倍数；ALT50_FLASH_PUSH=0 可关。
  echo "[$(ts)] alt-top50-since-btc-ath(CoinGecko前50+Binance ATH日+Llama日价,无 Dune)..."
  if $VENV alt-top50-since-btc-ath/download.py; then
    echo "[$(ts)] alt-top50-since-btc-ath flash → Lark..."
    $VENV alt-top50-since-btc-ath/flash.py --push --history-scan \
      || echo "[$(ts)]   [WARN] alt-top50-since-btc-ath flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] alt-top50-since-btc-ath 失败,继续"
  fi

  echo "[$(ts)] 7/12 uni-burn(Dune 窄查询,缓存超过 20h 才执行)..."
  $VENV uni-burn/run_all.py || echo "[$(ts)]   [WARN] uni-burn 失败,继续"
  echo "[$(ts)] uni-burn flash → Lark（销毁不新鲜则只发历史阈值）..."
  $VENV uni-burn/flash.py --push --history-scan \
    || echo "[$(ts)]   [WARN] uni-burn flash 失败,继续"

  echo "[$(ts)] token-burns(CoinGecko / Pump / PONS / HYPE 余额,无新 Dune)..."
  $VENV token-burns/download.py || echo "[$(ts)]   [WARN] token-burns 失败,继续"
  echo "[$(ts)] hype assistance fund flash → Lark..."
  $VENV token-burns/hype_flash.py --push \
    || echo "[$(ts)]   [WARN] hype flash 失败,继续"
  echo "[$(ts)] us gov btc holdings flash → Lark..."
  $VENV crypto-treasuries/us_gov_btc_flash.py --push \
    || echo "[$(ts)]   [WARN] us gov btc flash 失败,继续"
  echo "[$(ts)] treasury book-return flash → Lark..."
  $VENV crypto-treasuries/book_return_flash.py --push \
    || echo "[$(ts)]   [WARN] book-return flash 失败,继续"
  echo "[$(ts)] corp btc holdings flash → Lark（上市公司合计持仓/占上限，最新一份）..."
  $VENV crypto-treasuries/corp_btc_holdings_flash.py --push \
    || echo "[$(ts)]   [WARN] corp-btc-holdings flash 失败,继续"

  echo "[$(ts)] 8/12 robinhood-chain(自有窄查询超 20h;贵查询 7~14 天才自跑;DefiLlama 手续费)..."
  if $VENV robinhood-chain/run_all.py; then
    echo "[$(ts)] robinhood-chain fees flash → Lark..."
    $VENV robinhood-chain/fees_flash.py --push --history-scan \
      || echo "[$(ts)]   [WARN] robinhood-chain fees flash 失败,继续"
    echo "[$(ts)] robinhood-chain activity wow flash → Lark..."
    $VENV robinhood-chain/activity_flash.py --push --history-scan \
      || echo "[$(ts)]   [WARN] robinhood-chain activity flash 失败,继续"
    echo "[$(ts)] robinhood-chain RH vs Base 日交易笔数(GrowThePie) → Lark..."
    if $VENV robinhood-chain/rh_base_tx_download.py; then
      $VENV robinhood-chain/rh_base_tx_flash.py --push \
        || echo "[$(ts)]   [WARN] rh-base-tx flash 失败,继续"
      mkdir -p /root/dashboard/public/json/robinhood-chain
      cp -a robinhood-chain/output/json/rh_base_txcount.json /root/dashboard/public/json/robinhood-chain/rh_base_txcount.json \
        || echo "[$(ts)]   [WARN] rh-base-tx copy 失败,继续"
      cp -a robinhood-chain/output/json/rh_base_tx.json /root/dashboard/public/json/robinhood-chain/rh_base_tx.json \
        || echo "[$(ts)]   [WARN] rh-base-tx page copy 失败,继续"
    else
      echo "[$(ts)]   [WARN] rh-base-tx download 失败,继续"
    fi
    echo "[$(ts)] robinhood-chain RWA flash → Lark..."
    $VENV robinhood-chain/rwa_flash.py --push --history-scan \
      || echo "[$(ts)]   [WARN] robinhood-chain RWA flash 失败,继续"
    echo "[$(ts)] stock-token compare（只读 Dune 缓存，不执行）..."
    if $VENV robinhood-chain/stock_compare_download.py; then
      $VENV robinhood-chain/stock_compare_flash.py --push \
        || echo "[$(ts)]   [WARN] stock compare flash 失败,继续"
      mkdir -p /root/dashboard/public/json/robinhood-chain
      cp -a robinhood-chain/output/json/stock_token_compare.json /root/dashboard/public/json/robinhood-chain/stock_token_compare.json \
        || echo "[$(ts)]   [WARN] stock compare json 镜像失败,继续"
      cp -a robinhood-chain/output/json/stock_compare.json /root/dashboard/public/json/robinhood-chain/stock_compare.json \
        || echo "[$(ts)]   [WARN] stock compare page copy 失败,继续"
    else
      echo "[$(ts)]   [WARN] stock compare 下载失败,继续"
    fi
  else
    echo "[$(ts)]   [WARN] robinhood-chain 失败,继续"
  fi

  # GMGN 需出网到 openapi.gmgn.ai（IPv4）；失败不阻断 misc。
  # 快讯双通道：【实时日常报告】每日一条 + 过线另发【历史阈值警报】
  # （|合计环比|≥15% 或 |单链环比|≥25%）；GMGN_FLASH_PUSH=0 可关。
  echo "[$(ts)] gmgn(Meme 多链 24h volume TopN)..."
  if $VENV gmgn/download.py; then
    _gmgn_push="${GMGN_FLASH_PUSH:-}"
    if [ -z "$_gmgn_push" ] && [ -f .env ]; then
      _gmgn_push="$(awk -F= '/^GMGN_FLASH_PUSH=/{v=$2} END{print v}' .env | tr -d '\"'\''')"
    fi
    if [ "${_gmgn_push:-1}" = "1" ]; then
      echo "[$(ts)] gmgn flash → Lark（日常 + 阈值）..."
      $VENV gmgn/flash.py --push || echo "[$(ts)]   [WARN] gmgn flash 推送失败,继续"
    fi
    unset _gmgn_push
  else
    echo "[$(ts)]   [WARN] gmgn 失败,继续"
  fi

  echo "[$(ts)] stablecoin-cards(Paymentscan 公开月表,无 Dune)..."
  $VENV stablecoin-cards/download.py || echo "[$(ts)]   [WARN] stablecoin-cards 失败,继续"

  echo "[$(ts)] gsr-core3(GSR 公开周报表格,无 Dune)..."
  $VENV gsr-core3/download.py || echo "[$(ts)]   [WARN] gsr-core3 失败,继续"

  echo "[$(ts)] spark-savings(Spark 官方储蓄存款,无 Dune)..."
  if $VENV spark-savings/download.py; then
    echo "[$(ts)] spark-savings flash → Lark（只发最新一天）..."
    $VENV spark-savings/flash.py --push \
      || echo "[$(ts)]   [WARN] spark-savings flash 失败,继续"
    mkdir -p /root/dashboard/public/json/spark-savings
    cp -f spark-savings/output/json/spark_savings.json /root/dashboard/public/json/spark-savings/spark_savings.json \
      || echo "[$(ts)]   [WARN] spark-savings json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] spark-savings 失败,继续"
  fi

  echo "[$(ts)] btc-gold(OKX 比特币日收盘 / 雅虎黄金期货,无 Dune)..."
  $VENV btc-gold/download.py || echo "[$(ts)]   [WARN] btc-gold 失败,继续"

  echo "[$(ts)] zec-spot(OKX 现货 + CoinGlass 市值/合约/爆仓,无 Dune)..."
  if $VENV zec-spot/download.py; then
    echo "[$(ts)] zec-spot flash → Lark..."
    $VENV zec-spot/flash.py --push --history-scan \
      || echo "[$(ts)]   [WARN] zec-spot flash 失败,继续"
    mkdir -p /root/dashboard/public/json/zec-spot
    cp -a zec-spot/output/json/zec_spot.json /root/dashboard/public/json/zec-spot/zec_spot.json \
      || echo "[$(ts)]   [WARN] zec-spot json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] zec-spot 失败,继续"
  fi

  echo "[$(ts)] lab-spot(OKX LAB-USDT-SWAP,无 Dune)..."
  if $VENV lab-spot/download.py; then
    echo "[$(ts)] lab-spot flash → Lark（仅过线新闻体）..."
    $VENV lab-spot/flash.py --push \
      || echo "[$(ts)]   [WARN] lab-spot flash 失败,继续"
    mkdir -p /root/dashboard/public/json/lab-spot
    cp -a lab-spot/output/json/lab_spot.json /root/dashboard/public/json/lab-spot/lab_spot.json \
      || echo "[$(ts)]   [WARN] lab-spot json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] lab-spot 失败,继续"
  fi

  echo "[$(ts)] spcx-liq(币安 SPCX 永续 + CoinGlass 24h 爆仓)..."
  if $VENV spcx-liq/flash.py --push; then
    mkdir -p /root/dashboard/public/json/spcx-liq
    cp -a spcx-liq/output/json/spcx_liq.json /root/dashboard/public/json/spcx-liq/spcx_liq.json \
      || echo "[$(ts)]   [WARN] spcx-liq json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] spcx-liq 失败,继续"
  fi

  echo "[$(ts)] gua-spot(CoinGecko Superfortune/GUA,无 Dune)..."
  if $VENV gua-spot/download.py; then
    echo "[$(ts)] gua-spot flash → Lark（仅过线新闻体）..."
    $VENV gua-spot/flash.py --push \
      || echo "[$(ts)]   [WARN] gua-spot flash 失败,继续"
    mkdir -p /root/dashboard/public/json/gua-spot
    cp -a gua-spot/output/json/gua_spot.json /root/dashboard/public/json/gua-spot/gua_spot.json \
      || echo "[$(ts)]   [WARN] gua-spot json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] gua-spot 失败,继续"
  fi

  echo "[$(ts)] quarter-returns 图（用已有 BTC/ETH 日价，无新请求）..."
  $VENV quarter-returns/write_page.py \
    || echo "[$(ts)]   [WARN] quarter-returns 图失败,继续"

  echo "[$(ts)] privacy-coins(CoinGecko Privacy Coins 板块,无 Dune)..."
  if $VENV privacy-coins/download.py; then
    echo "[$(ts)] privacy-coins flash → Lark（仅最新新闻体）..."
    $VENV privacy-coins/flash.py --push \
      || echo "[$(ts)]   [WARN] privacy-coins flash 失败,继续"
    mkdir -p /root/dashboard/public/json/privacy-coins
    cp -a privacy-coins/output/json/privacy_coins.json /root/dashboard/public/json/privacy-coins/privacy_coins.json \
      || echo "[$(ts)]   [WARN] privacy-coins json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] privacy-coins 失败,继续"
  fi

  echo "[$(ts)] strc-apxusd(Yahoo STRC + DefiLlama apxUSD,无 Dune)..."
  if $VENV strc-apxusd/download.py; then
    echo "[$(ts)] strc-apxusd flash → Lark（破面值/回撤新闻体）..."
    $VENV strc-apxusd/flash.py --push \
      || echo "[$(ts)]   [WARN] strc-apxusd flash 失败,继续"
    mkdir -p /root/dashboard/public/json/strc-apxusd
    cp -a strc-apxusd/output/json/strc_apxusd.json /root/dashboard/public/json/strc-apxusd/strc_apxusd.json \
      || echo "[$(ts)]   [WARN] strc-apxusd json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] strc-apxusd 失败,继续"
  fi

  echo "[$(ts)] mstr-price(Yahoo MSTR 跌破 100，仅自上向下穿过)..."
  if $VENV mstr-price/download.py; then
    $VENV mstr-price/flash.py --push \
      || echo "[$(ts)]   [WARN] mstr-price flash 失败,继续"
    mkdir -p /root/dashboard/public/json/mstr-price
    cp -a mstr-price/output/json/mstr_price.json \
      /root/dashboard/public/json/mstr-price/mstr_price.json \
      || echo "[$(ts)]   [WARN] mstr-price json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] mstr-price 失败,继续"
  fi

  echo "[$(ts)] near-intents(DefiLlama NEAR Intents TVL/成交,无 Dune)..."
  if $VENV near-intents/download.py; then
    echo "[$(ts)] near-intents flash → Lark（仅最新新闻体）..."
    $VENV near-intents/flash.py --push \
      || echo "[$(ts)]   [WARN] near-intents flash 失败,继续"
    mkdir -p /root/dashboard/public/json/near-intents
    cp -a near-intents/output/json/near_intents.json /root/dashboard/public/json/near-intents/near_intents.json \
      || echo "[$(ts)]   [WARN] near-intents json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] near-intents 失败,继续"
  fi

  echo "[$(ts)] aave-llama(DefiLlama Aave/V4/Horizon/GHO 近似月报,无 Dune)..."
  if $VENV aave-llama/download.py; then
    echo "[$(ts)] aave-llama flash → Lark..."
    $VENV aave-llama/flash.py --push \
      || echo "[$(ts)]   [WARN] aave-llama flash 失败,继续"
    mkdir -p /root/dashboard/public/json/aave-llama
    cp -a aave-llama/output/json/aave_llama.json /root/dashboard/public/json/aave-llama/aave_llama.json \
      || echo "[$(ts)]   [WARN] aave-llama json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] aave-llama 失败,继续"
  fi

  echo "[$(ts)] aave-v4-spokes(官方 GraphQL 存款/Spoke + Llama 近邻,无 Dune)..."
  if $VENV aave-v4-spokes/download.py; then
    echo "[$(ts)] aave-v4-spokes flash → Lark（ATH/近7日|%|≥15%，首日建线不推）..."
    $VENV aave-v4-spokes/flash.py --push \
      || echo "[$(ts)]   [WARN] aave-v4-spokes flash 失败,继续"
    mkdir -p /root/dashboard/public/json/aave-v4-spokes
    cp -a aave-v4-spokes/output/json/aave_v4_spokes.json /root/dashboard/public/json/aave-v4-spokes/aave_v4_spokes.json \
      || echo "[$(ts)]   [WARN] aave-v4-spokes json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] aave-v4-spokes 失败,继续"
  fi

  echo "[$(ts)] cb-base-stock-dex(Coinbase 股票代币 Base 池子 24h 成交,无 Dune)..."
  if $VENV cb-base-stock-dex/download.py; then
    echo "[$(ts)] cb-base-stock-dex flash → Lark..."
    $VENV cb-base-stock-dex/flash.py --push --history-scan \
      || echo "[$(ts)]   [WARN] cb-base-stock-dex flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] cb-base-stock-dex 失败,继续"
  fi

  echo "[$(ts)] defi-tvl(DefiLlama 全球/分链 TVL,无 Dune)..."
  if $VENV defi-tvl/download.py; then
    echo "[$(ts)] defi-tvl flash → Lark..."
    $VENV defi-tvl/flash.py --push --history-scan \
      || echo "[$(ts)]   [WARN] defi-tvl flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] defi-tvl 失败,继续"
  fi

  echo "[$(ts)] fragment(DefiLlama Fragment 协议费用,无 Dune)..."
  if $VENV fragment/download.py; then
    echo "[$(ts)] fragment flash → Lark（只发最新完整日）..."
    $VENV fragment/flash.py --push \
      || echo "[$(ts)]   [WARN] fragment flash 失败,继续"
    mkdir -p /root/dashboard/public/json/fragment
    cp -a fragment/output/json/fragment_fees.json \
      /root/dashboard/public/json/fragment/fragment_fees.json \
      || echo "[$(ts)]   [WARN] fragment json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] fragment 失败,继续"
  fi

  echo "[$(ts)] web3-hacks(DefiLlama Hacks 月损,非 GoPlus,无 Dune)..."
  if $VENV web3-hacks/download.py; then
    echo "[$(ts)] web3-hacks flash → Lark（日常闭合月 + 阈值）..."
    $VENV web3-hacks/flash.py --push --history-scan \
      || echo "[$(ts)]   [WARN] web3-hacks flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] web3-hacks 失败,继续"
  fi

  echo "[$(ts)] dex-volume(DefiLlama 全球/分链 DEX 现货成交,非 CryptoRank)..."
  if $VENV dex-volume/download.py; then
    echo "[$(ts)] dex-volume flash → Lark..."
    $VENV dex-volume/flash.py --push --history-scan \
      || echo "[$(ts)]   [WARN] dex-volume flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] dex-volume 失败,继续"
  fi

  echo "[$(ts)] prop-amm(DefiLlama Solana Prop 协议份额 + ETH 三家)..."
  if $VENV prop-amm/flash.py --push; then
    mkdir -p /root/dashboard/public/json/prop-amm
    cp -a prop-amm/output/json/prop_amm.json /root/dashboard/public/json/prop-amm/prop_amm.json \
      || echo "[$(ts)]   [WARN] prop-amm json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] prop-amm 失败,继续"
  fi

  echo "[$(ts)] eth-quarter(GrowThePie 以太坊季度，滚动窗口落盘)..."
  if $VENV eth-quarter/flash.py --push; then
    mkdir -p /root/dashboard/public/json/eth-quarter
    cp -a eth-quarter/output/json/eth_quarter.json /root/dashboard/public/json/eth-quarter/eth_quarter.json \
      || echo "[$(ts)]   [WARN] eth-quarter json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] eth-quarter 失败,继续"
  fi

  echo "[$(ts)] btc-weekly-move(Yahoo BTC-USD 周日到周日)..."
  if $VENV btc-weekly-move/download.py; then
    echo "[$(ts)] btc-weekly-move flash → Lark..."
    $VENV btc-weekly-move/flash.py --push --history-scan \
      || echo "[$(ts)]   [WARN] btc-weekly-move flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] btc-weekly-move 失败,继续"
  fi

  echo "[$(ts)] cb-premium(CoinGlass Coinbase 比特币溢价日线)..."
  if $VENV cb-premium/download.py; then
    echo "[$(ts)] cb-premium flash → Lark..."
    $VENV cb-premium/flash.py --push --history-scan \
      || echo "[$(ts)]   [WARN] cb-premium flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] cb-premium 失败,继续"
  fi

  echo "[$(ts)] l2-token-mcap(CoinGecko Layer 2 分类市值,非 CryptoRank)..."
  if $VENV l2-token-mcap/download.py; then
    echo "[$(ts)] l2-token-mcap flash → Lark..."
    $VENV l2-token-mcap/flash.py --push --history-scan \
      || echo "[$(ts)]   [WARN] l2-token-mcap flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] l2-token-mcap 失败,继续"
  fi

  echo "[$(ts)] depin-mcap(CoinGecko DePIN 分类市值,非 CryptoRank)..."
  if $VENV depin-mcap/download.py; then
    echo "[$(ts)] depin-mcap flash → Lark..."
    $VENV depin-mcap/flash.py --push \
      || echo "[$(ts)]   [WARN] depin-mcap flash 失败,继续"
    mkdir -p /root/dashboard/public/json/depin-mcap
    cp -a depin-mcap/output/json/depin_mcap.json /root/dashboard/public/json/depin-mcap/depin_mcap.json \
      || echo "[$(ts)]   [WARN] depin-mcap json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] depin-mcap 失败,继续"
  fi

  # DefiLlama Yields：Pareto FalconX Credit Vault TVL。非 Token Terminal。只发最新，不回扫。
  echo "[$(ts)] falconx-vault(DefiLlama FalconX Credit Vault TVL)..."
  if $VENV falconx-vault/download.py; then
    echo "[$(ts)] falconx-vault flash → Lark..."
    $VENV falconx-vault/flash.py --push \
      || echo "[$(ts)]   [WARN] falconx-vault flash 失败,继续"
    mkdir -p /root/dashboard/public/json/falconx-vault
    cp -a falconx-vault/output/json/falconx_vault.json \
      /root/dashboard/public/json/falconx-vault/falconx_vault.json \
      || echo "[$(ts)]   [WARN] falconx-vault json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] falconx-vault 失败,继续"
  fi

  # RootData 融资明细加总：已收完自然年的 300万–1000万 / >1000万 轮次份额。非 CryptoRank。
  # 只发最新已收全年，收盘超 90 天不发（防滞后）；不回扫更早年份。
  echo "[$(ts)] financing-deal-bucket(RootData 融资额度档位份额)..."
  if $VENV financing-deal-bucket/download.py; then
    echo "[$(ts)] financing-deal-bucket flash → Lark..."
    $VENV financing-deal-bucket/flash.py --push \
      || echo "[$(ts)]   [WARN] financing-deal-bucket flash 失败,继续"
    mkdir -p /root/dashboard/public/json/financing-deal-bucket
    cp -a financing-deal-bucket/output/json/financing_deal_bucket.json \
      /root/dashboard/public/json/financing-deal-bucket/financing_deal_bucket.json \
      || echo "[$(ts)]   [WARN] financing-deal-bucket json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] financing-deal-bucket 失败,继续"
  fi

  # RootData 早期 vs 后期融资季度聚合。非 CryptoRank。只发最新已收季，收盘超 60 天不发。
  echo "[$(ts)] financing-early-stage(RootData 早期融资季度)..."
  if $VENV financing-early-stage/download.py; then
    echo "[$(ts)] financing-early-stage flash → Lark..."
    $VENV financing-early-stage/flash.py --push \
      || echo "[$(ts)]   [WARN] financing-early-stage flash 失败,继续"
    mkdir -p /root/dashboard/public/json/financing-early-stage
    cp -a financing-early-stage/output/json/financing_early_stage.json \
      /root/dashboard/public/json/financing-early-stage/financing_early_stage.json \
      || echo "[$(ts)]   [WARN] financing-early-stage json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] financing-early-stage 失败,继续"
  fi

  # RootData 上半年轮次（总/种子+Pre-Seed/A+）。非 CryptoRank。只发最新已收 H1，6/30 后超 60 天不发。
  echo "[$(ts)] financing-h1(RootData 上半年轮次，仅最新 H1 且 60 天内)..."
  if $VENV financing-h1/download.py; then
    $VENV financing-h1/flash.py --push \
      || echo "[$(ts)]   [WARN] financing-h1 flash 失败,继续"
    mkdir -p /root/dashboard/public/json/financing-h1
    cp -a financing-h1/output/json/financing_h1.json \
      /root/dashboard/public/json/financing-h1/financing_h1.json \
      || echo "[$(ts)]   [WARN] financing-h1 json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] financing-h1 失败,继续"
  fi

  echo "[$(ts)] rh-meme-mcap(CoinGecko Robinhood Chain Meme 分类市值)..."
  if $VENV rh-meme-mcap/download.py; then
    echo "[$(ts)] rh-meme-mcap flash → Lark（仅最新新闻体）..."
    $VENV rh-meme-mcap/flash.py --push \
      || echo "[$(ts)]   [WARN] rh-meme-mcap flash 失败,继续"
    mkdir -p /root/dashboard/public/json/rh-meme-mcap
    cp -a rh-meme-mcap/output/json/rh_meme_mcap.json /root/dashboard/public/json/rh-meme-mcap/rh_meme_mcap.json \
      || echo "[$(ts)]   [WARN] rh-meme-mcap json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] rh-meme-mcap 失败,继续"
  fi

  # Yahoo 美现货 BTC ETF 日成交量×收盘价加总（非 Glassnode）。只发最新交易日，不回扫。
  echo "[$(ts)] btc-etf-volume(Yahoo 美现货 BTC ETF 日成交)..."
  if $VENV btc-etf-volume/download.py; then
    echo "[$(ts)] btc-etf-volume flash → Lark..."
    $VENV btc-etf-volume/flash.py --push \
      || echo "[$(ts)]   [WARN] btc-etf-volume flash 失败,继续"
    mkdir -p /root/dashboard/public/json/btc-etf-volume
    cp -a btc-etf-volume/output/json/btc_etf_volume.json \
      /root/dashboard/public/json/btc-etf-volume/btc_etf_volume.json \
      || echo "[$(ts)]   [WARN] btc-etf-volume json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] btc-etf-volume 失败,继续"
  fi

  echo "[$(ts)] etf-halfyear(CoinGlass 日净流加总半年度 BTC/ETH,无新 API)..."
  if $VENV etf-halfyear/download.py; then
    echo "[$(ts)] etf-halfyear flash → Lark（只发最新已收完半年度，超90天不发）..."
    $VENV etf-halfyear/flash.py --push \
      || echo "[$(ts)]   [WARN] etf-halfyear flash 失败,继续"
    mkdir -p /root/dashboard/public/json/etf-halfyear
    cp -a etf-halfyear/output/json/etf_halfyear.json \
      /root/dashboard/public/json/etf-halfyear/etf_halfyear.json \
      || echo "[$(ts)]   [WARN] etf-halfyear json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] etf-halfyear 失败,继续"
  fi

  # 币安官方 U 本位永续 SPCX 24h 成交排名（免费 fapi，非 CoinGlass）。过线才推：进前三或完整日环比≥200%。
  echo "[$(ts)] binance-spcx-rank(币安 SPCX 永续成交排名)..."
  if $VENV binance-spcx-rank/download.py; then
    echo "[$(ts)] binance-spcx-rank flash → Lark..."
    $VENV binance-spcx-rank/flash.py --push \
      || echo "[$(ts)]   [WARN] binance-spcx-rank flash 失败,继续"
    mkdir -p /root/dashboard/public/json/binance-spcx-rank
    cp -a binance-spcx-rank/output/json/binance_spcx_rank.json \
      /root/dashboard/public/json/binance-spcx-rank/binance_spcx_rank.json \
      || echo "[$(ts)]   [WARN] binance-spcx-rank json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] binance-spcx-rank 失败,继续"
  fi

  # CoinGecko 只有 24h BTC 成交量。每天存一笔再乘比特币价格，第二天才有美元涨幅。
  # 第一天 flash 自己跳过。|涨幅|≥100% 另发历史阈值。不回扫外电旧涨幅。
  echo "[$(ts)] kr-cex-volume(Upbit/Bithumb 24h,无 Dune)..."
  if $VENV kr-cex-volume/download.py; then
    echo "[$(ts)] kr-cex-volume flash → Lark..."
    $VENV kr-cex-volume/flash.py --push \
      || echo "[$(ts)]   [WARN] kr-cex-volume flash 失败,继续"
    mkdir -p /root/dashboard/public/json/kr-cex-volume
    cp -a kr-cex-volume/output/json/kr_cex_volume.json \
      /root/dashboard/public/json/kr-cex-volume/kr_cex_volume.json \
      || echo "[$(ts)]   [WARN] kr-cex-volume json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] kr-cex-volume 失败,继续"
  fi

  echo "[$(ts)] perp-dex-oi(DefiLlama open-interest,无 Dune)..."
  if $VENV perp-dex-oi/download.py; then
    echo "[$(ts)] perp-dex-oi flash → Lark..."
    $VENV perp-dex-oi/flash.py --push \
      || echo "[$(ts)]   [WARN] perp-dex-oi flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] perp-dex-oi 失败,继续"
  fi

  echo "[$(ts)] edgex-oi(DefiLlama edgeX 未平仓)..."
  if $VENV edgex-oi/download.py; then
    echo "[$(ts)] edgex-oi flash → Lark（仅最新新闻体）..."
    $VENV edgex-oi/flash.py --push \
      || echo "[$(ts)]   [WARN] edgex-oi flash 失败,继续"
    mkdir -p /root/dashboard/public/json/edgex-oi
    cp -a edgex-oi/output/json/edgex_oi.json /root/dashboard/public/json/edgex-oi/edgex_oi.json \
      || echo "[$(ts)]   [WARN] edgex-oi json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] edgex-oi 失败,继续"
  fi

  echo "[$(ts)] scroll-l2(DefiLlama Scroll 锁仓+链收入,无 Dune)..."
  if $VENV scroll-l2/download.py; then
    echo "[$(ts)] scroll-l2 flash → Lark..."
    $VENV scroll-l2/flash.py --push \
      || echo "[$(ts)]   [WARN] scroll-l2 flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] scroll-l2 失败,继续"
  fi

  echo "[$(ts)] bitmine-eth(公开周持仓,无 Dune)..."
  if $VENV bitmine-eth/download.py; then
    echo "[$(ts)] bitmine-eth flash → Lark..."
    $VENV bitmine-eth/flash.py --push \
      || echo "[$(ts)]   [WARN] bitmine-eth flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] bitmine-eth 失败,继续"
  fi

  echo "[$(ts)] strive-btc(CoinGecko Strive/SpaceX/Coinbase 公开持仓)..."
  if $VENV strive-btc/download.py; then
    echo "[$(ts)] strive-btc flash → Lark（持仓跳变才发）..."
    $VENV strive-btc/flash.py --push \
      || echo "[$(ts)]   [WARN] strive-btc flash 失败,继续"
    mkdir -p /root/dashboard/public/json/strive-btc
    cp -a strive-btc/output/json/strive_btc.json /root/dashboard/public/json/strive-btc/strive_btc.json \
      || echo "[$(ts)]   [WARN] strive-btc json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] strive-btc 失败,继续"
  fi

  echo "[$(ts)] robinhood-usde(DefiLlama Robinhood Chain USDe,无 Dune)..."
  if $VENV robinhood-usde/download.py; then
    echo "[$(ts)] robinhood-usde flash → Lark..."
    $VENV robinhood-usde/flash.py --push \
      || echo "[$(ts)]   [WARN] robinhood-usde flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] robinhood-usde 失败,继续"
  fi

  echo "[$(ts)] xrp-oi(CoinGlass XRP/CME 未平仓,无 Dune)..."
  if $VENV xrp-oi/download.py; then
    echo "[$(ts)] xrp-oi flash → Lark..."
    $VENV xrp-oi/flash.py --push \
      || echo "[$(ts)]   [WARN] xrp-oi flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] xrp-oi 失败,继续"
  fi

  echo "[$(ts)] xrp-fees(CoinMetrics XRP 日手续费 90 日均值,无 Dune)..."
  if $VENV xrp-fees/flash.py --push; then
    mkdir -p /root/dashboard/public/json/xrp-fees
    cp -a xrp-fees/output/json/xrp_fees.json /root/dashboard/public/json/xrp-fees/xrp_fees.json \
      || echo "[$(ts)]   [WARN] xrp-fees json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] xrp-fees 失败,继续"
  fi

  echo "[$(ts)] btc-network(CoinMetrics TxCnt + mempool.space,无微交易占比/无 Dune)..."
  if $VENV btc-network/download.py; then
    echo "[$(ts)] btc-network flash → Lark..."
    $VENV btc-network/flash.py --push \
      || echo "[$(ts)]   [WARN] btc-network flash 失败,继续"
    mkdir -p /root/dashboard/public/json/btc-network
    cp -a btc-network/output/json/btc_network.json \
      /root/dashboard/public/json/btc-network/btc_network.json \
      || echo "[$(ts)]   [WARN] btc-network json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] btc-network 失败,继续"
  fi

  echo "[$(ts)] x402(x402stats 日笔数/成交额,非 Artemis/无 BlockRun/无 Dune)..."
  if $VENV x402/download.py; then
    echo "[$(ts)] x402 flash → Lark..."
    $VENV x402/flash.py --push \
      || echo "[$(ts)]   [WARN] x402 flash 失败,继续"
    mkdir -p /root/dashboard/public/json/x402
    cp -a x402/output/json/x402_stats.json \
      /root/dashboard/public/json/x402/x402_stats.json \
      || echo "[$(ts)]   [WARN] x402 json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] x402 失败,继续"
  fi

  echo "[$(ts)] options-expiry(CoinGlass Deribit 最近到期,无 Dune; Lark 在 08:35 期权脚本)..."
  $VENV options-expiry/download.py \
    || echo "[$(ts)]   [WARN] options-expiry 失败,继续"

  echo "[$(ts)] rh-chain-fees(DefiLlama 链手续费,无 Dune)..."
  if $VENV rh-chain-fees/download.py; then
    echo "[$(ts)] rh-chain-fees flash → Lark..."
    $VENV rh-chain-fees/flash.py --push \
      || echo "[$(ts)]   [WARN] rh-chain-fees flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] rh-chain-fees 失败,继续"
  fi

  echo "[$(ts)] arc-app-fees(DefiLlama Arc/对照链应用手续费近似,无 Dune)..."
  if $VENV arc-app-fees/download.py; then
    echo "[$(ts)] arc-app-fees flash → Lark..."
    $VENV arc-app-fees/flash.py --push --history-scan \
      || echo "[$(ts)]   [WARN] arc-app-fees flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] arc-app-fees 失败,继续"
  fi

  echo "[$(ts)] token-buyback-supply(DefiLlama 持有人收入折代币 vs 流通量,无 Dune)..."
  if $VENV token-buyback-supply/download.py; then
    echo "[$(ts)] token-buyback-supply flash → Lark..."
    $VENV token-buyback-supply/flash.py --push \
      || echo "[$(ts)]   [WARN] token-buyback-supply flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] token-buyback-supply 失败,继续"
  fi

  echo "[$(ts)] protocol-revenue-ytd(DefiLlama 观察名单 YTD 收入榜近似,无 Dune)..."
  if $VENV protocol-revenue-ytd/download.py; then
    echo "[$(ts)] protocol-revenue-ytd flash → Lark..."
    $VENV protocol-revenue-ytd/flash.py --push --history-scan \
      || echo "[$(ts)]   [WARN] protocol-revenue-ytd flash 失败,继续"
    echo "[$(ts)] collector-crypt 月收入 ATH flash → Lark..."
    $VENV collector-crypt/flash.py --push \
      || echo "[$(ts)]   [WARN] collector-crypt flash 失败,继续"
    mkdir -p /root/dashboard/public/json/collector-crypt
    cp -a collector-crypt/output/json/collector_crypt_monthly.json \
      /root/dashboard/public/json/collector-crypt/collector_crypt_monthly.json \
      2>/dev/null || echo "[$(ts)]   [WARN] collector-crypt json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] protocol-revenue-ytd 失败,继续"
  fi

  echo "[$(ts)] physical-tcg(DefiLlama Physical TCG 近30日费用新高,无 Dune)..."
  $VENV physical-tcg/flash.py --push \
    || echo "[$(ts)]   [WARN] physical-tcg flash 失败,继续"
  mkdir -p /root/dashboard/public/json/physical-tcg
  cp -a physical-tcg/output/json/physical_tcg.json \
    /root/dashboard/public/json/physical-tcg/physical_tcg.json \
    2>/dev/null || echo "[$(ts)]   [WARN] physical-tcg json 镜像失败,继续"

  echo "[$(ts)] protocol-revenue-monthly(DefiLlama 自然月收入 Top15,无 Dune)..."
  if $VENV protocol-revenue-monthly/download.py; then
    echo "[$(ts)] protocol-revenue-monthly flash → Lark..."
    $VENV protocol-revenue-monthly/flash.py --push --history-scan \
      || echo "[$(ts)]   [WARN] protocol-revenue-monthly flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] protocol-revenue-monthly 失败,继续"
  fi

  echo "[$(ts)] protocol-revenue-daily(DefiLlama 全市场 fees/revenue 日序列+周环比,无 Dune)..."
  $VENV protocol-revenue-daily/download.py \
    || echo "[$(ts)]   [WARN] protocol-revenue-daily 失败,继续"
  echo "[$(ts)] Base 日收入跻身 Top10 flash → Lark（DefiLlama dailyRevenue，重新进榜才发）..."
  if $VENV protocol-revenue-daily/base_top10_flash.py --push; then
    mkdir -p /root/dashboard/public/json/protocol-revenue-daily
    cp -a protocol-revenue-daily/output/json/base_daily_revenue_rank.json \
      /root/dashboard/public/json/protocol-revenue-daily/base_daily_revenue_rank.json \
      || echo "[$(ts)]   [WARN] base_daily_revenue_rank 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] base-top10 flash 失败,继续"
  fi

  echo "[$(ts)] perp-volume(CEX 合约日成交派生,DefiLlama derivatives 需 Pro)..."
  $VENV perp-volume/download.py \
    || echo "[$(ts)]   [WARN] perp-volume 失败(需先有 cex futures JSON),继续"

  echo "[$(ts)] lookonchain-week(自有口径周报,非 Lookonchain)..."
  $VENV lookonchain-week/flash.py --push --history-scan \
    || echo "[$(ts)]   [WARN] lookonchain-week flash 失败,继续"

  echo "[$(ts)] 9/12 mining-shutdown-price(Bitmain+mempool+2Miners,跳过 BitInfoCharts)..."
  $VENV mining-shutdown-price/run_all.py --skip-bitinfocharts \
    || echo "[$(ts)]   [WARN] mining-shutdown-price 失败,继续"

  echo "[$(ts)] 10/12 cex-asset&vol(CoinGecko vol + DefiLlama assets/BTC)..."
  $VENV "cex-asset&vol/run_all.py" --vol-mode append \
    || echo "[$(ts)]   [WARN] cex-asset&vol 失败,继续"
  echo "[$(ts)] binance-assets flash → Lark（仅标注总资产单日/两日骤降）..."
  $VENV cex-reserve/binance_assets_flash.py --push \
    || echo "[$(ts)]   [WARN] binance-assets flash 失败,继续"

  echo "[$(ts)] hl-cex-share flash(HL 占 CEX 合约样本月度占比 ATH 新闻)..."
  $VENV perp-volume/hl_cex_share_flash.py --push \
    || echo "[$(ts)]   [WARN] hl-cex-share flash 失败,继续"

  echo "[$(ts)] perp-dex-share(PerpFinder Perp DEX 月成交 + CoinGecko HL 份额)..."
  if $VENV perp-dex-share/flash.py --push; then
    mkdir -p /root/dashboard/public/json/perp-dex-share
    cp -a perp-dex-share/output/json/perp_dex_share.json /root/dashboard/public/json/perp-dex-share/perp_dex_share.json \
      || echo "[$(ts)]   [WARN] perp-dex-share json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] perp-dex-share 失败,继续"
  fi

  echo "[$(ts)] tokenized-treasuries 发行平台份额 → Lark（当日最新一份）..."
  if $VENV tokenized-treasuries/flash.py --push; then
    mkdir -p /root/dashboard/public/json/tokenized-treasuries
    cp -a tokenized-treasuries/output/json/treasury_platforms.json /root/dashboard/public/json/tokenized-treasuries/treasury_platforms.json \
      || echo "[$(ts)]   [WARN] treasury platforms json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] treasury platforms 失败,继续"
  fi

  echo "[$(ts)] eth-mmf BENJI/BUIDL 以太坊市值（rwa.xyz，领先变更才发）..."
  if $VENV eth-mmf/download.py; then
    mkdir -p /root/dashboard/public/json/eth-mmf
    cp -a eth-mmf/output/json/eth_mmf.json /root/dashboard/public/json/eth-mmf/eth_mmf.json \
      || echo "[$(ts)]   [WARN] eth-mmf json 镜像失败,继续"
    $VENV eth-mmf/flash.py --push \
      || echo "[$(ts)]   [WARN] eth-mmf flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] eth-mmf 失败,继续"
  fi

  echo "[$(ts)] eth-tokenized-treasuries（rwa.xyz Ethereum 合计，ATH/大涨才发）..."
  if $VENV eth-tokenized-treasuries/download.py; then
    mkdir -p /root/dashboard/public/json/eth-tokenized-treasuries
    cp -a eth-tokenized-treasuries/output/json/eth_tokenized_treasuries.json \
      /root/dashboard/public/json/eth-tokenized-treasuries/eth_tokenized_treasuries.json \
      || echo "[$(ts)]   [WARN] eth-tokenized-treasuries json 镜像失败,继续"
    $VENV eth-tokenized-treasuries/flash.py --push \
      || echo "[$(ts)]   [WARN] eth-tokenized-treasuries flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] eth-tokenized-treasuries 失败,继续"
  fi

  echo "[$(ts)] prediction-markets(DefiLlama 季度名义成交)..."
  if $VENV prediction-markets/download.py; then
    cp -a prediction-markets/output/json/prediction_markets_quarterly.json \
      /root/dashboard/public/json/prediction_markets_quarterly.json \
      || echo "[$(ts)]   [WARN] prediction-markets json 镜像失败,继续"
  else
    echo "[$(ts)]   [WARN] prediction-markets 失败,继续"
  fi

  echo "[$(ts)] pm-weekly(近7日成交与未平仓，只推最新一条)..."
  if $VENV pm-weekly/download.py; then
    mkdir -p /root/dashboard/public/json/pm-weekly
    cp -a pm-weekly/output/json/pm_weekly.json \
      /root/dashboard/public/json/pm-weekly/pm_weekly.json \
      || echo "[$(ts)]   [WARN] pm-weekly json 镜像失败,继续"
    $VENV pm-weekly/flash.py --push \
      || echo "[$(ts)]   [WARN] pm-weekly flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] pm-weekly 失败,继续"
  fi

  echo "[$(ts)] bitfinex-btc-longs(公开持仓,无 Dune)..."
  if $VENV bitfinex-btc-longs/download.py; then
    mkdir -p /root/dashboard/public/json/bitfinex-btc-longs
    cp -a bitfinex-btc-longs/output/json/longs.json /root/dashboard/public/json/bitfinex-btc-longs/longs.json \
      || echo "[$(ts)]   [WARN] bitfinex longs json 镜像失败,继续"
    $VENV bitfinex-btc-longs/flash.py --push \
      || echo "[$(ts)]   [WARN] bitfinex longs flash 失败,继续"
  else
    echo "[$(ts)]   [WARN] bitfinex-btc-longs 失败,继续"
  fi

  echo "[$(ts)] 11/12 派生 parquet → /root/outbox ..."
  $VENV to_parquet_misc.py --modules btc-index crypto-treasuries options stablecoin mining-shutdown-price \
    || echo "[$(ts)]   [WARN] to_parquet_misc 失败,继续"

  echo "[$(ts)] 12/12 镜像 json → dashboard(供网页) ..."
  for only in btc-index treasuries options stablecoin hyperliquid uni-burn token-burns robinhood-chain gmgn mining-shutdown-price cex dex-fees uniswap-volume uniswap-revenue chain-fees-dist chain-app-revenue alt-top50-since-btc-ath stablecoin-cards gsr-core3 btc-gold defi-tvl dex-volume l2-token-mcap btc-weekly-move cb-premium perp-dex-oi scroll-l2 bitmine-eth robinhood-usde xrp-oi rh-chain-fees options-expiry arc-app-fees protocol-revenue-ytd token-buyback-supply protocol-revenue-daily protocol-revenue-monthly perp-volume; do
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
