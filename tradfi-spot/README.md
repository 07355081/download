# tradfi-spot — 传统参考价 vs 加密 CEX TradFi 溢价（live-only）

## 产物

| 文件 | 用途 |
|------|------|
| `output/json/tradfi-arb-live.json` | **当下套利** last 溢价快照（覆写，不存档） |
| `output/json/tradfi-spot-universe.json` | 本轮动态 Top 100 + 排名 |

不再产出 `tradfi-spot-price/`、`tradfi-arb-spread/` 日线历史。

## 动态 Top 100

按已有 `tradfi-price/*_1d.json` **最近一根 K 线的 `volume_usd`** 跨所求和，分板块取 Top N：

- Stocks：70
- Commodities：15
- Indices：15

配置见 [`universe.json`](universe.json)（`sector_limits`、`yahoo_map`、`blocklist`）。**不为排名额外下载**。

掉榜标的不会出现在 live 快照中；旧日线文件会被硬删。

加密所 volume/OI/funding 仍走 [`../tradfi/`](../tradfi/)（含 **Hyperliquid HIP-3** 直连，见 [`../tradfi/HYPERLIQUID.md`](../tradfi/HYPERLIQUID.md)），本模块不重下。

## 运行

```bash
cd /root/data-download

# 每小时快照（主路径）
safe-run bash run_tradfi_spot_snapshot.sh

# 或单独调试
./.venv/bin/python tradfi-spot/snapshot_once.py

# 一次性清理废弃日线产物（可选）
./.venv/bin/python tradfi-spot/_cleanup.py --dashboard /root/dashboard/public/json
```

`run_tradfi_daily.sh` **不再**链式调用 tradfi-spot 日线。

## 安全护栏

- 无常驻进程；快照 timeout 180s
- 全局 `flock`（`.locks/tradfi-spot.lock`）；与 coinglass/misc 日更互斥
- `_watermark.py`：`free < 5G` 则 skip
- 经 `safe-run` / `batch.slice` 运行

## 口径

- **非实时、非可成交**；Yahoo 免费源可能有延迟
- 商品用近月期货（`GC=F` 等）作参考，与 `XAUUSDT` 有合约基差
- 只比加密所 **perp**；默认间隔 3600s（cron 控制）

## copy_to_dashboard

```bash
./.venv/bin/python copy_to_dashboard.py --only tradfi-spot-live --dest /root/dashboard/public/json
```

会同步 `tradfi-arb-live.json`、`tradfi-spot-universe.json`，并删除 dashboard 上废弃的日线目录。
