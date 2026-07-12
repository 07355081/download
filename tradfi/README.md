# 14 家交易所 TradFi / 股票 API 调研

> 对应 `coinglass-history/_common.py` 中 `TRACKED_EXCHANGES` 的 14 家交易所。  
> Coinglass 侧合约列表见 `coinglass_tradfi_instruments.json`（由 `symbols/*_instruments.json` 按 TradFi 规则筛出）。

## 结论速览

| 交易所 | 有 TradFi 产品 | 有专门 TradFi API | 主要产品形态 | 与 crypto 共用 API |
|--------|:-------------:|:-----------------:|--------------|:------------------:|
| Binance | ✅ | 部分（TradFi Perps 协议） | **永续** + 代币化现货 | 永续走 fapi；**bStocks 走现货 API（币安确认）** |
| Gate | ✅ | ✅ **`/tradfi/*`** | **永续** + **MT5 CFD** + 代币化现货 | 永续/现货/crypto 分开；CFD 独立 TradFi API |
| Coinbase | ✅ | 无独立 namespace | **永续**（INTX）+ 现货/经纪股票 | INTX `/api/v1/*`；股票走 Advanced/经纪 |
| Bybit | ✅ | 部分（V5 `symbolType` + MT5 独立） | **永续** + **MT5 CFD** + xStock 现货 | 永续/xStock 走 V5；CFD 走 MT5 |
| OKX | ✅ | 无独立 namespace，有 **`instCategory`** | **永续** + 部分现货 | 统一 `/api/v5/*` |
| Bitget | ✅ | 无独立 namespace，有 **`isRwa`** | **永续** + Ondo 代币化现货 | 统一 `/api/v2/mix/*` + spot |
| MEXC | ✅ | 无 | **永续**（含大量 `*STOCK*`） | 统一 `contract.mexc.com` |
| Crypto.com | ✅ | 无 | **永续** | 统一 Exchange API |
| Kucoin | ❌ | ❌ | — | Coinglass 追踪列表中 futures/spot 均为 0 |
| HTX | ✅ | 无 | **永续** | 统一 `linear-swap-api` |
| Kraken | ✅ | 无 | **永续/类 CFD**（`PF_*`）+ 现货 | Futures API + Spot API |
| Bitfinex | △ | ❌ | 仅 **XAUT 现货** | 普通 spot API |
| Upbit | ❌ | ❌ | — | Coinglass 追踪列表中无数据 |
| Deribit | ❌ | ❌ | 仅 crypto 期权 | — |

**永续 vs CFD 要点：**

- **永续（Perp）**：USDT 本位、资金费率、与 crypto 永续同一套下单/行情接口；Binance / Bybit / OKX / Bitget / Gate futures / MEXC / HTX / Crypto.com / Coinbase INTX / Kraken 均属此类（或极接近）。
- **CFD（MT5）**：Gate、Bybit 另有 **MetaTrader 5** 引擎的 forex / 指数 / 股票 CFD，**API 与 crypto 永续完全分离**（Gate 有 REST `/tradfi/*`；Bybit MT5 需 MT5 终端或专用集成，不走 V5 下单）。
- **代币化股票（Spot）**：Binance bStocks、Bybit xStock、Gate/Bitget Ondo 类 spot，走 spot API 或专用 RWA bapi，**不是永续也不是 MT5 CFD**。

---

## 按交易所详情

### 1. Binance — 永续 + bStocks

**产品**

- **TradFi 永续**：Coinglass 可见 SPX、XAU/XAG、原油、美股 perp（如 `TSLAUSDT`、`NVDAUSDT`）。
- **bStocks**：BEP-20 代币化美股（如 `TSLABUSDT`、`NVDABUSDT`），1:1 托管映射。

**API**

| 用途 | 端点 | 说明 |
|------|------|------|
| TradFi 永续协议 | `POST /fapi/v1/stock/contract` | 签署 TradFi-Perps 协议后才能交易 |
| 永续合约信息/行情/K线 | `GET /fapi/v1/exchangeInfo`、`/fapi/v1/klines` 等 | 与 crypto USDS-M 相同 namespace |
| **bStocks 枚举/K线/成交量** | `GET /api/v3/exchangeInfo`、`/api/v3/klines`、`/api/v3/ticker/24hr` | **币安确认：与 crypto 现货同一套 API** |
| bStocks 批量历史 | `https://data.binance.vision/data/spot/...` | 无需 Key；本机 `data-api.binance.vision` 可通 |
| bStocks 链上元数据(可选) | `GET /bapi/defi/v1/public/.../token/rwa/*` | 合规/链上状态；**行情数据不必走此路** |

**形态**：永续（TradFi Perps）+ 代币化现货（bStocks）。**无 MT5 CFD REST API**。

**项目内已有**：`tag/classify_coinglass_instruments.py` 未覆盖 Binance 原生分类；Coinglass 已含 TradFi 合约。

---

### 2. Gate — 永续 + 独立 TradFi（MT5 CFD）+ 代币化现货

**产品**

- **永续**：`XAU_USDT`、`SPX500_USDT`、`NAS100_USDT`、部分股票 perp。
- **MT5 CFD**：`XAUUSD` 等，category 含 Metals / Forex / Indices / Stocks。
- **Spot 代币化**：`GET /spot/currencies` 的 `category` 字段含 `stocks`(331)、`indices`、`metals`、`forex`、`commodities`。

**API（专门 TradFi namespace）**

| 端点 | 说明 |
|------|------|
| `GET /api/v4/tradfi/symbols/categories` | CFD 品种分类（Metals 等） |
| `GET /api/v4/tradfi/symbols` | CFD 品种列表（如 `XAUUSD`） |
| `GET /api/v4/tradfi/symbols/detail` | 品种详情（需鉴权） |
| `GET /api/v4/tradfi/symbols/{symbol}/tickers` | 行情 |
| `GET /api/v4/tradfi/orders`、`/tradfi/positions` 等 | 下单/持仓（MT5） |
| `GET /api/v4/spot/currencies` | Spot 币种 `category` 含 stocks |
| 永续 | `/api/v4/futures/usdt/*` 等，与 crypto 相同 |

**形态**：**三种并存** — USDT 永续、MT5 CFD（独立 API）、spot 代币化股票。

**项目内已有**：`classify_coinglass_instruments.py` 的 `build_gate_index()` 已用 `category` 打 Stocks 标签。

---

### 3. Coinbase — INTX 永续 + 经纪股票

**产品**

- **INTX 永续**：`TSLA-PERP`、`SPX-PERP`、`GOLD-PERP` 等（Coinglass futures）。
- **现货/经纪**：Advanced 界面美股 ETF 交易（FINRA 实体），与 INTX 不同产品线。

**API**

| 用途 | Base | 关键端点 |
|------|------|----------|
| 国际永续 | `https://api.international.coinbase.com` | `GET /api/v1/instruments`、`/api/v1/...` candles |
| 美国现货/股票 | Coinbase Advanced / Retail API | 非 INTX；需单独集成 |

**形态**：永续（INTX）+ 传统经纪股票。**无 `/tradfi/*` 专用路径**。

---

### 4. Bybit — 永续 + MT5 CFD + xStock

**产品**

- **TradFi 永续**：`symbolType=stock` / `commodity`（如 `TSLAUSDT`、`XAUUSDT`）。
- **xStock 现货**：`symbolType=xstocks`（如 `TSLAXUSDT`、`AAPLXUSDT`）。
- **MT5 CFD**：外汇、指数、股票 CFD，最高杠杆与 perp 不同。

**API**

| 用途 | 端点 | 说明 |
|------|------|------|
| 发现 TradFi 永续/xStock | `GET /v5/market/instruments-info?category=linear&symbolType=stock` | 股票永续 |
| 商品永续 | `...&symbolType=commodity` | XAU/XAG/原油 |
| xStock | `...&category=spot&symbolType=xstocks` | 代币化股票 |
| 签署协议 | `POST /v5/user/contract/agreement` | TradFi 相关 category |
| 交易 | `/v5/order/*` | 与 crypto 相同 |
| MT5 CFD | MT5 终端 / TradFi 专用集成 | **不走 V5 下单** |

文档：https://bybit-exchange.github.io/docs/v5/tradfi-integration

**形态**：**永续 + CFD 双轨**；API 层仅永续/xStock 在 V5 内可通过 `symbolType` 筛选。

---

### 5. OKX — 永续，`instCategory` 分类

**产品**

- 股票永续：`AAPL-USDT-SWAP`、`TSLA-USDT-SWAP` 等（`instCategory=3`）。
- 商品：`XAU-USDT-SWAP`（`instCategory=4`）。
- 外汇/债券：`instCategory=5/6`（如有）。

**API**

```
GET https://www.okx.com/api/v5/public/instruments?instType=SWAP|SPOT|FUTURES
```

响应字段 `instCategory`：

| 值 | 含义 |
|----|------|
| `1` | Crypto |
| `3` | Stocks |
| `4` | Commodities |
| `5` | Forex |
| `6` | Bonds |

**形态**：**永续**（无 MT5 CFD REST）。**无独立 `/tradfi` 路径**，靠 `instCategory` 区分。

**项目内已有**：`classify_coinglass_instruments.py` 的 `OKX_INST_CATEGORY` 与 `build_okx_index()`。

---

### 6. Bitget — 永续（`isRwa`）+ Ondo 现货

**产品**

- **Stock/RWA 永续**：`TSLAUSDT`、`NVDAUSDT`、`SPXUSDT`；部分符号带 `*STOCK*` 后缀。
- **现货**：Ondo 类代币化（如 `TSLAONUSDT`）。

**API**

```
GET https://api.bitget.com/api/v2/mix/market/contracts?productType=USDT-FUTURES
```

- 响应含 `isRwa: "YES"` 可筛 RWA/TradFi 永续。
- 现货走标准 `/api/v2/spot/*`。

**形态**：**永续** + 代币化现货。**无 MT5 CFD API**。

---

### 7. MEXC — 永续为主（TradFi 品种最多之一）

**产品**

- Coinglass futures 中 **~155 个 stock 类**（`*STOCK_USDT` 命名）。
- 另含 XAU、SPX500、NAS100、外汇对等。

**API**

```
GET https://contract.mexc.com/api/v1/contract/detail
GET https://contract.mexc.com/api/v1/contract/kline/{symbol}
```

**形态**：**USDT 永续**，与 crypto 同一 contract API。**无独立 TradFi namespace**。

---

### 8. Crypto.com — INTX 风格永续

**产品**：`AAPLUSD-PERP`、`XAUUSD-PERP`、`SPXUSD-PERP` 等。

**API**

```
GET https://api.crypto.com/exchange/v1/public/get-instruments
GET https://api.crypto.com/exchange/v1/public/get-candlestick
```

**形态**：**永续**，统一 Exchange API。

---

### 9. Kucoin — 当前无（在 Coinglass 追踪集内）

Coinglass `futures_instruments.json` / `spot_instruments.json` 中 **Kucoin 条目为 0**。  
KuCoin 生态有 RWA/股票类产品宣传，但 **不在本项目 14 所 Coinglass 合约清单内**，暂无对应下载管道。

---

### 10. HTX — 永续

**产品**：`SPX500-USDT`、`XAU-USDT`、原油、美股 perp。

**API**

```
GET https://api.hbdm.com/linear-swap-api/v1/swap_contract_info
GET https://api.hbdm.com/linear-swap-ex/market/history/kline
```

**形态**：**USDT 线性永续**，与 crypto 相同 API。

---

### 11. Kraken — Futures `PF_*` + 现货

**产品**

- 永续/类 CFD：`PF_SPXUSD`、`PF_EURUSD`、`PF_WTIOILUSD`、`PF_TSLAXUSD` 等。
- 现货：`PAXG/USD`、`XAUT/USD`、`EUR/USD` 等。

**API**

| 市场 | Base | 端点 |
|------|------|------|
| Futures | `https://futures.kraken.com/derivatives/api/v3` | `/instruments`、`/tickers`、`/history` |
| Spot | `https://api.kraken.com/0/public` | `AssetPairs`、`OHLC` |

**形态**：Futures 侧为 **crypto-native 永续引擎上的 TradFi 合约**（非 MT5）；另有 xStocks 现货叙事。

---

### 12. Bitfinex — 几乎无 TradFi

Coinglass 仅 **XAUT 现货**。无 TradFi 永续专用 API。

---

### 13. Upbit — 无

Coinglass 追踪列表 futures/spot 均为 0（韩国所，TradFi 衍生品不在当前数据范围）。

---

### 14. Deribit — 无

仅 crypto 期权（BTC/ETH 等），Coinglass futures 26 个均为 crypto，**无 TradFi**。

---

## Coinglass 与本项目的关系

当前 `data-download/coinglass-history` **没有单独的 TradFi 端点**；TradFi 合约与 crypto 混在同一套接口里：

- 合约列表：`/api/futures/supported-exchange-pairs`、`/api/spot/supported-exchange-pairs`
- 历史 OHLC：`futures-price-history`、`spot-price-history` 等（按 `exchange` + `symbol` 拉取）

因此：**只要 Coinglass 收录了某 TradFi 符号，现有 downloader 就能拉 K 线**；需要的是 **符号筛选/打标**，而不是新 Coinglass API。

`tag/classify_coinglass_instruments.py` 已通过 OKX `instCategory`、Gate `category` 识别 Stocks；可扩展 Binance/Bybit `symbolType` 等。

---

## 推荐：按形态选原生 API（若绕过 Coinglass）

| 目标 | 优先交易所 | 发现方式 |
|------|-----------|----------|
| USDT TradFi 永续 K 线 | OKX, Bybit, Bitget, MEXC | `instCategory` / `symbolType` / `isRwa` |
| MT5 CFD K 线/交易 | Gate, Bybit | Gate `/tradfi/symbols` + tickers |
| 代币化股票 spot | Gate, Bybit, Binance bStocks | spot currencies / xstocks / RWA bapi |
| 指数/商品/外汇 perp | OKX, Kraken, HTX | 同上 + Kraken `PF_*` |

---

## 文件说明

| 文件 | 内容 |
|------|------|
| `README.md` | 本调研文档 |
| `coinglass_tradfi_instruments.json` | 14 所在 Coinglass 符号库中的 TradFi 合约统计（按 index/metal/oil/forex/stock 分类） |
| `exchange_api_survey.json` | 机器可读：API 端点、产品形态、是否有专用 TradFi API |

生成 `coinglass_tradfi_instruments.json`：

```bash
python tradfi/generate_coinglass_summary.py
```

（或直接阅读已生成的 JSON。）

---

## 实时 API 探测（2026-07-07）

| 探测目标 | 结果 |
|----------|------|
| Gate `GET /tradfi/symbols/categories` | ✅ 返回 Metals 等 CFD 分类 |
| Gate `GET /tradfi/symbols` | ✅ 返回 `XAUUSD`（Gold CFD）等 |
| Binance `GET /fapi/v1/exchangeInfo` | ⚠️ 公开接口未列出 TradFi 符号（0 条）；Coinglass 侧仍有 SPX/XAU/股票 perp，可能需签署协议或受地区限制 |
| HTX / Crypto.com | 本次网络超时，未拿到响应；Coinglass 符号库中两者均有 TradFi 合约 |
