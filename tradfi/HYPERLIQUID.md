# Hyperliquid 数据管道

## 分工

| 数据 | 来源 | 模块 |
|------|------|------|
| **Crypto 永续**（BTC/ETH…） | Coinglass | `coinglass-history/`（`TRACKED_EXCHANGES` 含 Hyperliquid） |
| **TradFi HIP-3**（`xyz:NVDA` 等） | HL 官方 info API | `tradfi/exchanges.py` → `Hyperliquid` adapter |
| **宏观统计**（日 volume/OI 聚合） | Dune | `hyperliquid/`（看板层，非 operational） |
| **live 套利快照** | HL `allMids` + Yahoo | `tradfi-spot/cex_tickers.py` |

Coinglass 侧 TradFi base 仍由 `is_tradfi_base()` 排除，与 10 所 CEX 口径一致。

## API

- Base：`POST https://api.hyperliquid.xyz/info`
- discover：`perpDexs` → 各 dex `metaAndAssetCtxs`，只保留 `name` 含 `:` 的 HIP-3 市场
- 跳过 crypto HIP-3（`hyna`；或 universe 一半以上为已知加密 base）；`BTC/ETH` 等即使 `native_stock=True` 也不进 TradFi
- price：`candleSnapshot`（500 根/页，`_MAX_PAGES=12` 封顶）
- OI / funding：`metaAndAssetCtxs` 快照 + `fundingHistory`

符号：`instrument_id=xyz:NVDA`，`base_asset=NVDA`，输出 `tradfi-price/Hyperliquid_xyz-NVDA_1d.json`。

## 安全护栏

- `download_workers=2`（HL 保守并发；其余所仍 `--workers 6`）
- 日更经 `run_tradfi_daily.sh`：`flock` + `_watermark.py` + cron `safe-run` → `batch.slice`
- `tradfi-spot`：bulk `allMids`，禁止逐 symbol K 线
- 首次 HL 全量勿与日更 cron 叠跑；分 `--exchanges Hyperliquid --limit-per-exchange N` 调试

## 用法

```bash
cd /root/data-download

# 只发现 HIP-3 TradFi 标的
./.venv/bin/python tradfi/download.py --exchanges Hyperliquid --discover-only

# 试拉 3 个标的 1d
./.venv/bin/python tradfi/download.py --exchanges Hyperliquid --limit-per-exchange 3 --intervals 1d

# Coinglass 刷新 HL crypto 符号（需 API key）
./.venv/bin/python coinglass-history/_coin_pairs.py futures
```

## 本机 dashboard

需在 `dashboard/src/lib/coinglass-exchanges.ts` 的 `TRACKED_EXCHANGES` 加 `"Hyperliquid"`（VPS 下载仓已加，前端自行同步）。
