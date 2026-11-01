# defi-tvl（DefiLlama 全球 / 分链 DeFi TVL）

免费接口，**不占 Dune**。口径不含流动性质押与双计，与 CryptoRank 外电数字可能不一致。

## 用法

```bash
python defi-tvl/download.py
python defi-tvl/flash.py                 # dry-run
python defi-tvl/flash.py --push --history-scan
```

日更由 `run_misc_daily.sh` 调用；JSON 镜像到 `public/json/defi-tvl/defi_tvl.json`。

## Lark 档位

| 抬头 | 触发 |
|------|------|
| 【实时日常报告】 | 每个最新完整日一条 |
| 【历史阈值警报】 | 全球日环比 \|Δ\|≥3% 或主力链 ≥8%（刚越过） |
| 【季度特殊警报】 | 连三月同向、连三季下跌后回升、单链季环比 ≥80% 等 |

环境变量可调：`DEFI_TVL_GLOBAL_DOD_PCT`、`DEFI_TVL_CHAIN_DOD_PCT`、`DEFI_TVL_CHAIN_QOQ_PCT`。
