# dex-volume（DefiLlama 全球 / 分链 DEX 现货成交）

免费接口，**不占 Dune**。口径为 DefiLlama `dailyVolume`，**不是 CryptoRank**；数字可能与外电不一致，发稿须写明来源。

## 用法

```bash
python dex-volume/download.py
python dex-volume/flash.py                 # dry-run
python dex-volume/flash.py --push --history-scan
python copy_to_dashboard.py --only dex-volume
```

日更由 `run_misc_daily.sh` 调用；JSON 镜像到 `public/json/dex-volume/dex_volume.json`。

## Lark 档位

| 抬头 | 触发 |
|------|------|
| 【实时日常报告】 | 每个最新完整日一条 |
| 【历史阈值警报】 | 全球日环比 \|Δ\|≥15% 或主力链 ≥25%（刚越过） |
| 【季度特殊警报】 | 连三季下跌后回升、年内首次季环比转正、全球/链季环比极端 |

环境变量：`DEX_VOLUME_GLOBAL_DOD_PCT`、`DEX_VOLUME_CHAIN_DOD_PCT`、`DEX_VOLUME_GLOBAL_QOQ_PCT`、`DEX_VOLUME_CHAIN_QOQ_PCT`。
