# Layer 2 代币总市值（CoinGecko）

免费接口，**不占 Dune**。口径为 CoinGecko 分类 `layer-2`（Layer 2 (L2)）的 `market_cap`，**不是 CryptoRank**；数字可能与外电不一致，发稿须写明来源。

免费 API 只有当日快照；历史序列靠本机日更累加，**无法回放** CryptoRank 稿里的「7 月末 70 亿」等外电历史点。

## 运行

```bash
python download.py
python flash.py              # dry-run
python flash.py --push --history-scan
```

挂在 `run_misc_daily.sh`；镜像到 `public/json/l2-token-mcap/l2_token_mcap.json`。

## Lark

- 【实时日常报告】：总市值 + Top 币 + 日环比
- 【历史阈值警报】：|日环比|≥5%（`L2_TOKEN_MCAP_DOD_PCT`），或创已存序列新高/新低
- 新高 / 从已存低点大幅反弹：额外加【特殊警报】
