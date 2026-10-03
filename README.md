# data-download

为 `data-dashboard` 提供离线数据的 Python 管道。

## 布局

```
<module>/
  download.py          # 拉取 API → cache/*.csv
  csv_to_json.py       # cache → output/json/*.json
  run_all.py           # 可选：串联上述步骤
  cache/               # CSV 中间缓存
  output/json/         # 发布前 JSON（与 dashboard public/json 子目录一致）
```

`coinglass-history/<子模块>/` 与 `public/json/<子模块>/` 一一对应。

## CSV 缺失时从 JSON 读断点（增量下载）

`coinglass-history/_common.py` 中 `read_ohlc_cache` / `read_wide_cache`：

- 先读 `cache/<stem>.csv`
- **若无 CSV**，自动读同模块 `output/json/<stem>.json`（只读，不写 dashboard）
- 环境变量 `DATA_DOWNLOAD_JSON_CACHE_FALLBACK=0` 可关闭

`cex-asset&vol/`（VPS 日更，经 `run_misc_daily.sh`）：

- 中间产物在 `cache/`：`major_vol_切勿删除.csv`、`btc_hourly_data.csv`、`btc_daily_vwap.csv`、`cex_total_assets_daily_wide.csv`、`cex_exchange_daily_balance_btc.csv`、`cex_dynamic_factors_daily.csv`、`cex_volume_pipeline_unified.xlsx`
- 发布面仅 `output/json/` → `copy_to_dashboard.py --only cex` → `public/json/cex-asset-vol/`
- `1.coingecko-vol.py`：无 `cache/major_vol_切勿删除.csv` 则新建近一年；生产只用 **`append`**（补洞 + 最近 2 个 UTC 日 tip）。**禁止日常 `update` 刷近一年**（须同时加 `--i-know-this-rewrites-history`）。`major_vol` 不从 JSON 恢复。
- **闭合月冻结**：存在 `monthly-report/monthly_report_YYYY-MM.xlsx` 的月份为 closed。step4 写 pipeline 前会备份到 `cache/backups/`，再保留 closed 月日度/月度旧值；`csv_to_json` 对 closed 月 prior JSON 优先。无正式月报时行为与从前相同（全量重算开放历史）。
- **月报**：`build_monthly_report.py` 成交量上月读上月报、本月读 pipeline；发布即关账。
- **采样探针（只举证，不改策略）**：`probe_volume_stability.py` 小时采样固定历史日 → `cache/probes/volume_chart_samples.jsonl`；`--summary` 看漂移。cron：`run_volume_probe_hourly.sh`。采样结果**不自动**切换冻结/refresh；若要改代码须人工下令。
- `3.fetch_cex_total_assets_merged.py`：同一次 DefiLlama `/protocol/{slug}` 写 USD wide + 币余额 wide：原生 BTC、ETH 聚合（ETH+WETH+STETH+WEETH+METH+CMETH）、稳定币白名单合计；缺数据为空白非 0。

因此 **不必** 为全部序列预先跑完 CSV 反推，只要有 `output/json` 即可增量更新（`major_vol` 除外）。

`stablecoin/` 使用 `download_defillama.py`（DefiLlama）拉取链级与全网市值，再经 `csv_to_json.py` 发布。

## 发布到 dashboard

```bash
python copy_to_dashboard.py --dry-run
python copy_to_dashboard.py
```

默认目标：`C:\code\data-dashboard\public\json`

## 环境

复制 `.env.example` 为 `.env`，设置 `COINGLASS_API_KEY` 等。

```bash
pip install -r requirements.txt
```
