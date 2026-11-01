# CEX 合约（永续）日成交

DefiLlama `overview/derivatives` 免费接口返回 **402**，故从 `cex-asset&vol` 的  
`cex_exchange_daily_futures_volume_usd.json` 派生全球 `ALL` 日序列，并算周环比。

- **不另吃 CoinGlass / Dune**（依赖已有 CEX 管线）
- 输出：`output/json/perp_volume.json`
- 发稿写明 CEX 合约样本口径

```bash
# 需先有 cex futures 日 JSON
python perp-volume/download.py
```
