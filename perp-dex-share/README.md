# Perp DEX 月成交（PerpFinder / CoinGecko）

免费接口，**不占 Dune**，不买 DefiLlama Pro（`overview/derivatives` 为 402）。

| 口径 | 来源 |
|------|------|
| 全行业日历月成交 | PerpFinder `/api/data/perps-chart`（上游 DefiLlama 日成交） |
| Hyperliquid 日历月成交 / 份额 | CoinGecko `exchanges/hyperliquid/volume_chart` × BTC 日价 |
| Lighter / Aster / edgeX | PerpFinder `/api/data/perps` **近 30 日**快照（无这些所的免费日历月序列） |

## 用法

```bash
python perp-dex-share/flash.py
python perp-dex-share/flash.py --push
```

日更由 `run_misc_daily.sh` 调用。JSON 镜像到 `public/json/perp-dex-share/perp_dex_share.json`。

## Lark

只发**最近一个已结束自然月**，月结后 14 日内一条新闻体。不发日常流水，不回扫旧月。
