# OKX 储备金证明（PoR）

拉取 OKX 官网储备证明快照，落盘并月更推飞书。对齐 `binance-por/`。

## 数据

- 优先：`OKX_POR_API_URL`（VPS 浏览器 Network 确认的同源 JSON）
- 其次：内置候选 `priapi` / `v2` 路径
- 回退：解析 [详情页](https://www.okx.com/zh-hans/proof-of-reserves/detail) HTML
- 本机若 DNS/防火墙连不上 okx.com：用已种子的 `2026-08-11` / `2026-09-08` 做 dry-run
- 输出：`output/json/okx_por_latest.json`、`okx-por-history/YYYY-MM-DD.json`
- **不占** Dune / CoinGlass 额度

## 用法

```bash
python okx-por/download.py
python okx-por/flash.py                  # dry-run
python okx-por/flash.py --refresh --push # 拉数并推送
```

## Lark

- 【实时日常报告】：新一期快照（按 `audit_id`）一条吴说体
- 【历史阈值警报】：用户 BTC |Δ|≥5000，ETH |Δ|≥10万，USDT |Δ|≥3亿，或 BTC/ETH/USDT/USDC 任一储备率 <100%

环境变量：`OKX_POR_BTC_USER_DELTA`、`OKX_POR_ETH_USER_DELTA`、`OKX_POR_USDT_USER_DELTA`、`OKX_POR_MIN_RATIO`。

## 定时（VPS）

```cron
40 10 3-12 * * /root/data-download/okx-por/run_monthly.sh
```

上线前请在 VPS 打开 PoR 详情页 DevTools → Network，把真实 JSON 地址写入 `OKX_POR_API_URL`（可放 `/root/dashboard/.env.live`）。
