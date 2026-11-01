# Binance 储备金证明（PoR）

拉取 Binance 官网同源公开接口，落盘最新快照与历史归档；月更推飞书。

## 数据

- 接口：`POST /bapi/apex/v1/public/apex/market/query/userReserveAuditProofSnapshot`（body `{}`）
- 输出：`output/json/binance_por_latest.json`、`output/json/binance-por-history/YYYY-MM-DD.json`
- **不占** Dune / CoinGlass 额度

## 用法

```bash
python binance-por/download.py
python binance-por/flash.py                  # dry-run
python binance-por/flash.py --refresh --push # 拉数并推送
```

## Lark

- 【实时日常报告】：新一期快照（按 `audit_id`）一条正式新闻体
- 【历史阈值警报】：用户 BTC |Δ|≥5000，或 ETH |Δ|≥100000，或 BTC/ETH/BNB/USDT 任一储备率 <100%

环境变量可覆盖：`BINANCE_POR_BTC_USER_DELTA`、`BINANCE_POR_ETH_USER_DELTA`、`BINANCE_POR_MIN_RATIO`。

## 定时（VPS）

```cron
30 10 3-10 * * /root/data-download/binance-por/run_monthly.sh
```
