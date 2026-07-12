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

`1.coingecko-vol.py`：无 `major_vol_切勿删除.csv` 则新建并写入近一年 BTC；有文件则 `append` 补未下载格、`update` 覆盖近一年。`major_vol` 不从 JSON 恢复。`3.fetch_cex_total_assets_merged.py` 依赖 `cex_total_assets_daily_wide.csv` 做增量断点。

因此 **不必** 为全部序列预先跑完 CSV 反推，只要有 `output/json` 即可增量更新。

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
