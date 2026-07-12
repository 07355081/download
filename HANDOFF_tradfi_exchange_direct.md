# TradFi 交易所直连改造 —— 阶段一交接（本机执行结果）

> 生成时间：2026-07-12　执行机器：`E:\Code`（Windows / PowerShell）
> 决策：**方案 c** —— tradfi 标的（Stocks / Commodities / Indices）**完全从 coinglass 下线**，
> 改由**交易所直连独占**，落**独立目录** `public/json/tradfi-price/`。
> 本文档由 download 本机侧 agent 产出，供 **VPS 侧 cursor（agent-vps）** 判断如何在权威 dashboard 仓
> `wublock123/data-dashboard` 应用、构建、发布、验收。

---

## 0. 环境事实（影响可执行性，务必先读）

| 项 | 现状 |
|----|------|
| 代码根 | `E:\Code\data-download`（**是** git 仓，origin=`github.com/07355081/download`，branch=main）、`E:\Code\data-dashboard`（**不是** git 仓，无 `.git`） |
| 任务里写的 `C:\code\...` | 本机不存在，真实在 `E:\Code\...`（脚本已改为按同级目录自动定位） |
| docker | **未安装** → 本机无法 build/push 镜像 |
| rsync / bash | **无**（有 ssh / scp / wsl） → 本机无法跑 `scripts/rsync-json-to-prod.sh` |
| dashboard 本机构建 | `npm run build` 因**与 tradfi 无关的既存 TS 类型错**失败（详见 §5），tradfi 代码本身 webpack 编译通过、lint 无错 |
| native tradfi 数据 | `tradfi/output/json/tradfi-price/` 目前**为空**（下载尚未跑，见 Step 2/3） |

---

## 1. 已完成（download 侧，`07355081/download` 已改动的文件）

改动文件（`git status` 可见）：
```
 M coinglass-history/_common.py       # 新增 is_tradfi_base()/extract_base_from_instrument()（importlib 载入 tradfi 分类器）
 M coinglass-history/_runner.py       # run_per_instrument 排除 tradfi 标的
 M copy_to_dashboard.py               # 新增 tradfi 分支 + DEFAULT_DEST 自动定位
 M requirements.txt                   # 新增 PySocks（socks 代理）
 M tradfi/_common.py                  # CRYPTO_OVERRIDE 修误伤 + socks5h 代理支持
?? coinglass-history/_cleanup_tradfi.py  # 新增：清理 coinglass tradfi 存量脚本
```

### Step 1a —— coinglass 停 tradfi
- **过滤**：`coinglass-history/_runner.py::run_per_instrument` 在构建下载 tuple 时，对每个标的用
  `_common.is_tradfi_base(base_asset)` 判定，命中 Stocks/Commodities/Indices 的直接跳过并打印
  `[tradfi-excluded] skipped N ...`。判定**复用 `tradfi/_common.classify_sector`**，经
  `_common._load_tradfi_classifier()`（用 `importlib` 以独立模块名载入 tradfi/_common，避开与本包同名
  `_common` 冲突；并修了 Py3.14 dataclass 需先入 `sys.modules` 的坑）。该 runner 被
  **futures-price-history / futures-open-interest-history / spot-price-history** 三个 per-instrument 模块共用，
  故三者今后都不再下载 tradfi。
- **修误伤（重要）**：审计发现 `QNT`(Quant) 被 tag 误标 Stocks、`DIA` 在指数集里——两者是**真加密**。
  已在 **单一真源** `tradfi/_common.py` 加 `CRYPTO_OVERRIDE = {"QNT","DIA"}`，`classify_sector` 优先返回
  `None`（native 发现 + coinglass 排除 + dashboard route 三处一致生效）。如后续发现更多撞名，往该集合补即可。
- **清理存量**：`coinglass-history/_cleanup_tradfi.py`（dry-run 默认，`--apply` 删除；权威 base 取
  `symbols/{market}_instruments.json`，缺失按文件名兜底提取再判定；同时删 `cache/*.csv` 与 `output/json/*.json`，
  否则 csv_to_json 会从 cache 重生）。
  **已执行 `--apply`（三模块）**：删除 **13862** 个文件；复查残留 **0**；加密标的保留（已确认 `BTC/HMSTR/QNT` 仍在）。
  `futures-price-history/output/json` 由 16782 → 13551。
  > 注：download 仓 `.gitignore` 已排除 cache/output，故这些删除**不进 git**（数据本就不入库）。
  > dashboard 侧对应副本会在 `copy_to_dashboard`（mirror-delete）时自动清掉。

### Step 1b —— copy_to_dashboard 加 tradfi 分支
- `copy_to_dashboard.py` 新增 `tradfi` 数据集：把 `tradfi/output/json/tradfi-price/*.json`
  **1:1 镜像**到 `<dashboard>/public/json/tradfi-price/`（**独立目录**，mirror-delete 只作用于该目录，**不碰 coinglass**）。
- `DEFAULT_DEST` 改为按同级目录自动定位（`SCRIPT_DIR.parent/data-dashboard/public/json`），本机解析为
  `E:\Code\data-dashboard\public\json`；找不到才回退旧硬编码。仍可 `--dest` 覆盖。
- 用法：`python copy_to_dashboard.py --only tradfi`（或 `--only all`）。dry-run 已验证。

### Step 2 前置修复 —— socks 代理支持
- 原 `tradfi/_common.py` 用 `urllib.ProxyHandler`，**不支持 `socks5h`**，你计划的
  `ssh -D 1080` + `ALL_PROXY=socks5h://127.0.0.1:1080` 会静默失效。
- 已加 SOCKS 支持：`_proxy_handler()` 检测到 `socks*` 前缀时用 **PySocks** 的 `SocksiPyHandler`
  （socks5h → 远端解析 DNS）。已 `pip install PySocks` 并写入 `requirements.txt`。
  非代理路径与 socks 路径均已本机构造验证（未做真实连线）。

---

## 2. 已完成（dashboard 侧，代码需应用到 `wublock123/data-dashboard`）

本机 `E:\Code\data-dashboard` 非 git，无法从此推送。以下为**完整改动**，请 agent-vps 应用到权威仓后再
`npm run build` + 出镜像。

### 2.1 `src/app/api/coinglass/futures/tradfi-volume-overview/route.ts` —— 整文件替换

改动要点：
- `LOCAL_PRICE_HISTORY_DIR` 由 `futures-price-history` → **`tradfi-price`**。
- `loadPairsFromHistory` 逐文件读 native JSON 顶层 **`sector` meta** 做分类（不再依赖 tag 分类 / CSV）；
  `readLocalJson` 已按路径缓存，故随后取量不重复 IO。
- 分类模型由 tag label 改为 **native sector**；`available_labels = TradFi(全部聚合) + Stocks/Commodities/Indices`，
  `label` 为空或 `TradFi` 表示不过滤（兼容前端默认 `TradFi`）。
- 删除 tag/CSV 相关死代码；`category_source` 标为 `native-sector`；`source=csv` 仍返回 410。

<details><summary>完整 route.ts（点开复制）</summary>

```ts
import { promises as fs } from "fs";
import path from "path";
import { NextRequest, NextResponse } from "next/server";

import { LocalJsonNotFound, readLocalJson } from "@/lib/local-json";

type TradfiPair = {
  exchange: string;
  instrumentId: string;
  baseAsset: string;
  sector: string;
};

type NormalizedPoint = {
  time: number;
  value: number;
};

type SeriesItem = {
  name: string;
  latest_value: number;
  points: Array<{ time: number; value: number }>;
};

// 交易所直连独占：native 下载器输出到独立目录 public/json/tradfi-price/
// 文件命名 {Exchange}_{instrument_id}_{interval}.json，顶层含 sector meta。
const LOCAL_PRICE_HISTORY_DIR = "tradfi-price";
const LOCAL_PRICE_HISTORY_PATH = path.join(process.cwd(), "public", "json", LOCAL_PRICE_HISTORY_DIR);
const CACHE_DIR = path.join(process.cwd(), ".cache", "coinglass", "tradfi-volume-overview");
const CACHE_TTL_MS = 10 * 60 * 1000;
const CONCURRENCY = 4;
const DEFAULT_LIMIT = 180;
const MAX_LIMIT = 500;
const TOP_SYMBOLS = 15;
const TOP_EXCHANGES = 12;

// 默认聚合标签：等价于「全部 TradFi 标的」，不做 sector 过滤。
const AGGREGATE_LABEL = "TradFi";
// native sector meta 的取值域（大宗含金属；不含外汇/加密）。
const NATIVE_SECTORS = ["Stocks", "Commodities", "Indices"] as const;

const EXCHANGE_CANONICAL_MAP: Record<string, string> = {
  binance: "Binance",
  bitfinex: "Bitfinex",
  bitget: "Bitget",
  bitmart: "Bitmart",
  bybit: "Bybit",
  "crypto-com": "Crypto.com",
  "crypto.com": "Crypto.com",
  deribit: "Deribit",
  gate: "Gate",
  htx: "HTX",
  kucoin: "KuCoin",
  mexc: "MEXC",
  okx: "OKX",
  coinbase: "Coinbase",
  kraken: "Kraken",
  hyperliquid: "Hyperliquid",
};

const SYMBOL_SYNONYMS: Record<string, string> = {
  GOLD: "XAU",
  SILVER: "XAG",
  SP500: "SPX",
  SPX500: "SPX",
  NASDAQ100: "NAS100",
  NDX: "NAS100",
  WTI: "CL",
  USOIL: "CL",
  UKOIL: "BZ",
  BRENTOIL: "BRENT",
};

// 仅在 native JSON 缺失 sector meta 时作兜底推断（正常路径不会用到）。
const INDEX_SET = new Set([
  "SPX",
  "SPY",
  "QQQ",
  "SQQQ",
  "TQQQ",
  "NAS100",
  "US30",
  "DJ30",
  "RUSSELL",
  "DAX",
  "FTSE",
  "NIKKEI",
  "HSI",
]);
const COMMODITY_SET = new Set([
  "XAU",
  "XAG",
  "XPT",
  "XPD",
  "XAUT",
  "PAXG",
  "COPPER",
  "NATGAS",
  "BRENT",
  "CL",
  "BZ",
]);

const memoryCache = new Map<string, { expiresAt: number; payload: unknown }>();
const inFlightCache = new Map<string, Promise<unknown>>();

function normalizeUpper(value: string): string {
  return value.trim().toUpperCase();
}

function normalizeSymbol(value: string): string {
  const upper = normalizeUpper(value);
  return SYMBOL_SYNONYMS[upper] ?? upper;
}

/** 兜底 sector 推断（native meta 缺失时）：与 native 口径一致，金属归入 Commodities。 */
function inferSectorFallback(symbolRaw: string): string {
  const symbol = normalizeSymbol(symbolRaw);
  if (INDEX_SET.has(symbol)) return "Indices";
  if (COMMODITY_SET.has(symbol)) return "Commodities";
  return "Stocks";
}

function normalizeSector(raw: unknown, baseAsset: string): string {
  const value = String(raw ?? "").trim();
  if (value) {
    const lower = value.toLowerCase();
    if (lower === "metals") return "Commodities";
    // 归一化大小写到 native 取值域
    const matched = NATIVE_SECTORS.find((s) => s.toLowerCase() === lower);
    if (matched) return matched;
    return value;
  }
  return inferSectorFallback(baseAsset);
}

function toCanonicalExchange(exchange: string): string {
  const key = exchange.trim().toLowerCase();
  return EXCHANGE_CANONICAL_MAP[key] ?? exchange.trim();
}

function extractBaseAssetFromInstrumentId(instrumentId: string): string {
  const raw = instrumentId.trim();
  if (!raw) return "";
  if (raw.includes("-")) {
    return raw.split("-")[0] || "";
  }
  const quoteSuffixes = ["USDT", "USDC", "BUSD", "FDUSD", "USD"];
  for (const suffix of quoteSuffixes) {
    if (raw.endsWith(suffix) && raw.length > suffix.length) {
      return raw.slice(0, -suffix.length);
    }
  }
  return raw;
}

/** 枚举 tradfi-price 目录，逐文件读取 native sector meta，得到标的清单。 */
async function loadPairsFromHistory(interval: string): Promise<TradfiPair[]> {
  let files: string[];
  try {
    files = await fs.readdir(LOCAL_PRICE_HISTORY_PATH);
  } catch {
    return [];
  }
  const suffix = `_${interval}.json`;
  const dedup = new Map<string, TradfiPair>();
  for (const fileName of files) {
    if (!fileName.endsWith(suffix)) continue;
    const withoutSuffix = fileName.slice(0, -suffix.length);
    const sep = withoutSuffix.indexOf("_");
    if (sep <= 0) continue;
    const exchange = withoutSuffix.slice(0, sep).trim();
    const instrumentId = withoutSuffix.slice(sep + 1).trim();
    if (!exchange || !instrumentId) continue;
    const key = `${exchange}__${instrumentId}`;
    if (dedup.has(key)) continue;

    let sectorRaw: unknown;
    let baseAssetRaw = extractBaseAssetFromInstrumentId(instrumentId);
    try {
      const meta = await readLocalJson<{ sector?: unknown; base_asset?: unknown }>(
        `${LOCAL_PRICE_HISTORY_DIR}/${fileName}`
      );
      sectorRaw = meta?.sector;
      if (typeof meta?.base_asset === "string" && meta.base_asset.trim()) {
        baseAssetRaw = meta.base_asset.trim();
      }
    } catch {
      // 文件不可读时按文件名兜底
    }
    const baseAsset = normalizeSymbol(baseAssetRaw);
    if (!baseAsset) continue;
    dedup.set(key, {
      exchange,
      instrumentId,
      baseAsset,
      sector: normalizeSector(sectorRaw, baseAsset),
    });
  }
  return Array.from(dedup.values());
}

function normalizeNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function normalizeTimestamp(value: unknown): number | null {
  const parsed = normalizeNumber(value);
  if (parsed === null) return null;
  return parsed < 1e12 ? Math.trunc(parsed * 1000) : Math.trunc(parsed);
}

function normalizeVolumeRows(data: unknown): NormalizedPoint[] {
  if (!Array.isArray(data)) return [];
  return data
    .map((row) => {
      if (Array.isArray(row)) {
        const time = normalizeTimestamp(row[0]);
        const value = normalizeNumber(row[5]);
        if (time === null || value === null) return null;
        return { time, value };
      }
      if (!row || typeof row !== "object") return null;
      const record = row as Record<string, unknown>;
      const time =
        normalizeTimestamp(record.time) ??
        normalizeTimestamp(record.timestamp) ??
        normalizeTimestamp(record.date);
      const value = normalizeNumber(
        record.volume_usd ?? record.volumeUsd ?? record.vol_usd ?? record.quote_volume ?? record.volume
      );
      if (time === null || value === null) return null;
      return { time, value };
    })
    .filter((item): item is NormalizedPoint => Boolean(item))
    .sort((a, b) => a.time - b.time);
}

type ResampleInterval = "1w" | "1m" | "1y";

function isDailyResampledInterval(interval: string): interval is ResampleInterval {
  return interval === "1w" || interval === "1m" || interval === "1y";
}

/** 1w/1m/1y：UTC 自然周 / 自然月 / 自然年汇总；1m = 一月（非 1 分钟） */
function bucketTimeMs(timeMs: number, interval: ResampleInterval): number {
  const d = new Date(timeMs);
  if (interval === "1w") {
    const day = d.getUTCDay();
    const daysSinceMonday = (day + 6) % 7;
    return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() - daysSinceMonday);
  }
  if (interval === "1m") {
    return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
  }
  return Date.UTC(d.getUTCFullYear(), 0, 1);
}

function resampleVolumePoints(points: NormalizedPoint[], interval: ResampleInterval): NormalizedPoint[] {
  const bucket = new Map<number, number>();
  points.forEach((point) => {
    const key = bucketTimeMs(point.time, interval);
    bucket.set(key, (bucket.get(key) ?? 0) + point.value);
  });
  return Array.from(bucket.entries())
    .map(([time, value]) => ({ time, value }))
    .sort((a, b) => a.time - b.time);
}

function dailyFetchLimit(interval: string, limit: number): number {
  if (interval === "1w") return Math.min(limit * 7, 100_000);
  if (interval === "1m") return Math.min(limit * 31, 100_000);
  if (interval === "1y") return Math.min(limit * 366, 100_000);
  return limit;
}

function tailPoints(points: NormalizedPoint[], limit: number): NormalizedPoint[] {
  if (points.length <= limit) return points;
  return points.slice(points.length - limit);
}

async function fetchPairVolumeHistory(pair: TradfiPair, interval: string, limit: number) {
  const fileInterval = isDailyResampledInterval(interval) ? "1d" : interval;
  const exchange = toCanonicalExchange(pair.exchange);
  const file = `${LOCAL_PRICE_HISTORY_DIR}/${exchange}_${pair.instrumentId}_${fileInterval}.json`;
  let payload: { data?: unknown };
  try {
    payload = await readLocalJson<{ data?: unknown }>(file);
  } catch (err) {
    if (err instanceof LocalJsonNotFound) return [];
    throw err;
  }
  let rows = normalizeVolumeRows(payload?.data);
  const fetchLimit = dailyFetchLimit(interval, limit);
  rows = tailPoints(rows, fetchLimit);
  if (isDailyResampledInterval(interval)) {
    rows = resampleVolumePoints(rows, interval);
  }
  return tailPoints(rows, limit);
}

async function runWithConcurrency<T>(
  items: T[],
  worker: (item: T) => Promise<void>,
  concurrency: number
) {
  let cursor = 0;
  const workers = Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      await worker(items[index]);
    }
  });
  await Promise.all(workers);
}

function toSeriesItems(seriesMap: Map<string, Map<number, number>>): SeriesItem[] {
  return Array.from(seriesMap.entries())
    .map(([name, values]) => {
      const points = Array.from(values.entries())
        .sort((a, b) => a[0] - b[0])
        .map(([time, value]) => ({ time, value }));
      return {
        name,
        latest_value: points.at(-1)?.value ?? 0,
        points,
      };
    })
    .filter((item) => item.points.length > 0)
    .sort((a, b) => b.latest_value - a.latest_value || a.name.localeCompare(b.name));
}

function takeTopWithOther(items: SeriesItem[], topN: number): SeriesItem[] {
  if (items.length <= topN) return items;
  const top = items.slice(0, topN);
  const rest = items.slice(topN);
  const otherMap = new Map<number, number>();
  rest.forEach((item) => {
    item.points.forEach((point) => {
      otherMap.set(point.time, (otherMap.get(point.time) ?? 0) + point.value);
    });
  });
  const otherSeries: SeriesItem = {
    name: "Other",
    latest_value: Array.from(otherMap.values()).at(-1) ?? 0,
    points: Array.from(otherMap.entries())
      .sort((a, b) => a[0] - b[0])
      .map(([time, value]) => ({ time, value })),
  };
  return [...top, otherSeries];
}

function normalizeLimit(raw: string | null): number {
  const parsed = Number(raw);
  if (Number.isNaN(parsed)) return DEFAULT_LIMIT;
  return Math.min(Math.max(Math.floor(parsed), 1), MAX_LIMIT);
}

function normalizeLabel(value: string | null): string {
  return String(value ?? "").trim();
}

function safeFileSegment(value: string): string {
  return value.replace(/[^a-zA-Z0-9._-]+/g, (m) => encodeURIComponent(m).replace(/%/g, "_"));
}

function getDiskCachePath(interval: string, limit: number, label: string): string {
  const labelSegment = safeFileSegment(label || "_all_");
  return path.join(CACHE_DIR, `tag_${interval}_${limit}_${labelSegment}.json`);
}

async function readDiskCache(cacheKey: string, interval: string, limit: number, label: string) {
  const filePath = getDiskCachePath(interval, limit, label);
  try {
    const stat = await fs.stat(filePath);
    if (Date.now() - stat.mtimeMs > CACHE_TTL_MS) return null;
    const payload = JSON.parse(await fs.readFile(filePath, "utf-8"));
    memoryCache.set(cacheKey, { expiresAt: stat.mtimeMs + CACHE_TTL_MS, payload });
    return payload;
  } catch {
    return null;
  }
}

async function writeDiskCache(interval: string, limit: number, label: string, payload: unknown) {
  await fs.mkdir(CACHE_DIR, { recursive: true });
  await fs.writeFile(getDiskCachePath(interval, limit, label), JSON.stringify(payload), "utf-8");
}

/** 依据 native sector 统计可用标签：TradFi(全部) 置顶 + 各 sector 按标的数降序。 */
function computeAvailableLabels(allPairs: TradfiPair[]): Array<{ name: string; pair_count: number }> {
  const bySector = new Map<string, number>();
  allPairs.forEach((pair) => {
    bySector.set(pair.sector, (bySector.get(pair.sector) ?? 0) + 1);
  });
  const sectors = Array.from(bySector.entries())
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([name, pair_count]) => ({ name, pair_count }));
  return [{ name: AGGREGATE_LABEL, pair_count: allPairs.length }, ...sectors];
}

async function computeAvailableLabelsForInterval() {
  return computeAvailableLabels(await loadPairsFromHistory("1d"));
}

async function buildPayload(interval: string, limit: number, label: string) {
  const pairInterval = isDailyResampledInterval(interval) ? "1d" : interval;
  const allPairs = await loadPairsFromHistory(pairInterval);
  const availableLabels = computeAvailableLabels(allPairs);
  const useAll = !label || label === AGGREGATE_LABEL;
  const tradfiPairs = useAll ? allPairs : allPairs.filter((pair) => pair.sector === label);

  const categoryMap = new Map<string, Map<number, number>>();
  const symbolMap = new Map<string, Map<number, number>>();
  const exchangeMap = new Map<string, Map<number, number>>();
  let successCount = 0;
  let missingCount = 0;

  await runWithConcurrency(
    tradfiPairs,
    async (pair) => {
      try {
        const points = await fetchPairVolumeHistory(pair, interval, limit);
        if (points.length === 0) {
          missingCount += 1;
          return;
        }
        successCount += 1;
        const categoryName = pair.sector;
        const categorySeries = categoryMap.get(categoryName) ?? new Map<number, number>();
        const symbolSeries = symbolMap.get(pair.baseAsset) ?? new Map<number, number>();
        const exchangeSeries = exchangeMap.get(pair.exchange) ?? new Map<number, number>();

        points.forEach((point) => {
          categorySeries.set(point.time, (categorySeries.get(point.time) ?? 0) + point.value);
          symbolSeries.set(point.time, (symbolSeries.get(point.time) ?? 0) + point.value);
          exchangeSeries.set(point.time, (exchangeSeries.get(point.time) ?? 0) + point.value);
        });

        categoryMap.set(categoryName, categorySeries);
        symbolMap.set(pair.baseAsset, symbolSeries);
        exchangeMap.set(pair.exchange, exchangeSeries);
      } catch {
        missingCount += 1;
      }
    },
    CONCURRENCY
  );

  const categories = toSeriesItems(categoryMap);
  const symbols = takeTopWithOther(toSeriesItems(symbolMap), TOP_SYMBOLS);
  const exchanges = takeTopWithOther(toSeriesItems(exchangeMap), TOP_EXCHANGES);

  return {
    code: "0",
    msg: "success",
    data: {
      interval,
      category_source: "native-sector",
      selected_label: label || null,
      available_labels: availableLabels,
      limit,
      totals: {
        pair_count: tradfiPairs.length,
        success_count: successCount,
        missing_count: missingCount,
      },
      categories,
      symbols,
      exchanges,
      updated_at: Date.now(),
    },
  };
}

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const sourceRaw = searchParams.get("source")?.trim().toLowerCase();

  if (sourceRaw === "csv") {
    return NextResponse.json(
      {
        code: -1,
        msg: "source=csv 已下线；tradfi 现由交易所直连独占（native sector）",
        data: null,
      },
      { status: 410 }
    );
  }

  if (searchParams.get("labels_only") === "1") {
    const cacheKey = "labels_only:native";
    const memoryHit = memoryCache.get(cacheKey);
    if (memoryHit && memoryHit.expiresAt > Date.now()) {
      return NextResponse.json(memoryHit.payload);
    }
    try {
      const available_labels = await computeAvailableLabelsForInterval();
      const payload = { code: "0", msg: "success", data: { available_labels } };
      memoryCache.set(cacheKey, { expiresAt: Date.now() + CACHE_TTL_MS, payload });
      return NextResponse.json(payload);
    } catch (error: any) {
      return NextResponse.json(
        { code: -1, msg: error?.message ?? "标签列表加载失败", data: null },
        { status: 500 }
      );
    }
  }

  const interval = searchParams.get("interval")?.trim() || "1d";
  const limit = normalizeLimit(searchParams.get("limit"));
  const label = normalizeLabel(searchParams.get("label"));
  const cacheKey = `native:${interval}:${limit}:${label}`;

  const memoryHit = memoryCache.get(cacheKey);
  if (memoryHit && memoryHit.expiresAt > Date.now()) {
    return NextResponse.json(memoryHit.payload);
  }

  const diskHit = await readDiskCache(cacheKey, interval, limit, label);
  if (diskHit) {
    return NextResponse.json(diskHit);
  }

  const existingJob = inFlightCache.get(cacheKey);
  if (existingJob) {
    return NextResponse.json(await existingJob);
  }

  try {
    const job = buildPayload(interval, limit, label);
    inFlightCache.set(cacheKey, job);
    const payload = await job;
    memoryCache.set(cacheKey, { expiresAt: Date.now() + CACHE_TTL_MS, payload });
    try {
      await writeDiskCache(interval, limit, label, payload);
    } catch {
      // best effort
    }
    inFlightCache.delete(cacheKey);
    return NextResponse.json(payload);
  } catch (error: any) {
    inFlightCache.delete(cacheKey);
    return NextResponse.json(
      {
        code: -1,
        msg: error?.message ?? "TradFi 成交量总览加载失败",
        data: null,
      },
      { status: 500 }
    );
  }
}
```
</details>

### 2.2 `src/components/dashboard/TagPerpVolumeSection.tsx` —— 4 处小改（数据源标注 / 副标题）

把两处 `ChartSourceNote` 的 sources 由
`tag/coinglass_top200_留底.json` + `futures-price-history/...`
改为单条 `tradfi-price/...`；两处副标题 `（Coinglass 留底）` 改为 `（交易所直连）`：

```tsx
// Volume by Symbol / Volume by Exchange 两处 buildStackedBarOption：
subtitle: `CEX ${label} Perp（交易所直连）`,   // 原：（Coinglass 留底）

// 两处 <ChartSourceNote sources={[...]}/>：
sources={[
  { type: "local", path: "public/json/tradfi-price/<exchange>_<instrument_id>_<interval>.json" },
]}
// 原为 tag/coinglass_top200_留底.json + futures-price-history/... 两条
```

> 前端契约不变：仍用 `available_labels` / `symbols` / `exchanges` / `totals` / 默认 label=`TradFi`，
> 故 `TradfiDashboardClient.tsx` 无需改。

---

## 3. 暂无法在本机完成的步骤 & 原因

| 步骤 | 原因 | 谁来做 |
|------|------|--------|
| **Step 2/3 代理下载**（`python tradfi/download.py`） | 需本机对 VPS 建 `ssh -D 1080` SOCKS 隧道（**VPS 私钥在你处**，agent 无法代持/交互登录）；且全量下载数十分钟~小时级 | 你（本机）或直接在 VPS 上跑 downloader |
| **Step 4 rsync** | 本机**无 rsync/bash**；且 `scripts/rsync-json-to-prod.sh` 里路径 `/c/code/...` 与 key `/c/Users/Joeyw/...` 是原作者机器的，需按实机改 | 你 / agent-vps |
| **Step 4 docker build/push ACR** | 本机**未装 docker** | agent-vps（VPS 上有 docker + ACR 流程） |
| **Step 1c 应用到权威 dashboard + `npm run build`** | 本机 dashboard **非 git 且有既存无关构建错**（§5），不是权威源 | agent-vps 在 `wublock123/data-dashboard` 应用 §2 后构建 |
| **Step 5 git push（dashboard）** | 本机 dashboard 无 `.git` | agent-vps |

---

## 4. 待执行 Runbook（给你 / agent-vps）

> 前提：native tradfi 下载**能连 10 家交易所**——本机国内网络多数被封（见 `tradfi/方案-可行性与网络.md`）。
> 两种方式二选一：**(A)** 本机挂 `ssh -D` SOCKS 到 VPS 再跑；**(B)** 直接在 VPS 上跑 downloader（推荐，见可行性文档治根方案）。

### Step 2 —— 代理小样本验证（本机方式 A）
```powershell
# 终端 A（保持不关）：用能登录 VPS 的 key 建 SOCKS 隧道
ssh -i <你的VPS私钥> -D 1080 root@47.74.6.207

# 终端 B：
cd E:\Code\data-download
$env:ALL_PROXY = "socks5h://127.0.0.1:1080"   # 已加 PySocks 支持，socks5h 生效
python tradfi\download.py --limit-per-exchange 3
# 期望：10 家（含 Binance/Bybit）拿到真数据；JSON 含 time/volume_usd/sector
```

### Step 3 —— 生产全量（保持代理）
```powershell
python tradfi\download.py --intervals 1d
git add -A ; git commit -m "tradfi: exchange-direct 阶段一（备份）" ; git push   # download 个人仓，仅备份
```
> 数据质量待验（本机无数据未能做）：**逐所核对 volume_usd 口径**；
> **校准 Coinbase K线分页**（曾 HTTP 400，见 `exchanges.py::Coinbase.fetch_price` start/end）
> 与 **Crypto.com**（本机曾 SSL EOF，count=1000）。跑通 Step 2 后按真实响应校准。

### Step 4 —— 上传数据 + 出镜像
```powershell
# 4.1 生成 dashboard 数据（本机可做，一旦 tradfi/output 有数据）
cd E:\Code\data-download
python copy_to_dashboard.py            # 生成 public/json/tradfi-price（并 mirror-delete 掉 coinglass 里已清的 tradfi）

# 4.2 同步到 VPS（本机需先装 rsync，或改用 scp/在 VPS 侧拉）
#     现有 scripts/rsync-json-to-prod.sh 需把 SRC 改成 E:/Code/... 、key 改成本机 key
bash scripts/rsync-json-to-prod.sh     # 同步 tradfi-price（及其余）到 VPS

# 4.3 出镜像（在有 docker 的机器 / VPS，先在权威 dashboard 仓应用 §2 改动）
docker build -t crpi-fz0dnre7t4yztz62.cn-shanghai.personal.cr.aliyuncs.com/wushuo_docker/data-dashboard:<tag> .
docker push crpi-fz0dnre7t4yztz62.cn-shanghai.personal.cr.aliyuncs.com/wushuo_docker/data-dashboard:<tag>
```

### Step 5 —— 部署 + 验收（agent-vps）
- 在 `wublock123/data-dashboard` 应用 §2（route.ts 整替 + TagPerpVolumeSection 4 处），`npm run build` 应通过（权威仓若无 §5 那些既存错）。
- VPS 执行 `deploy-acr.sh` 拉新镜像重启。
- 验收（见 §6）。

---

## 5. 本机 dashboard 构建的既存问题（非 tradfi，供参考，本机未改）

本机 `E:\Code\data-dashboard` `npm run build` 依次遇到（均**与 tradfi 无关**）：
1. `@api/coinglass-api` 本地链接过期 → 本机已 `npm install` 修好（webpack 转为编译成功）。
2. 既存 TS 类型错（按你的 `hand_off` 选择**未改**、已回滚我临时的探查性修改）：
   - `src/components/dashboard/AggregatedVolumePanel.tsx`：`Interval` 仅 `"1h"|"4h"|"1d"`，却比较 `=== "1w"`（2 处，line 64 / 129）。
   - `src/components/dashboard/MiningShutdownPricePanel.tsx:131`：`.filter((item) => ...)` 参数 `item` 隐式 any。
   - （可能还有更多，type-check 首错即停。）

> 结论：这些是**本机这份 dashboard 副本**的问题；权威仓 `wublock123/data-dashboard` 若能正常构建，
> 直接在其上应用 §2 即可。若权威仓也有同样问题，需另行修（不属 tradfi 范围）。tradfi 的 route.ts 本身
> webpack 每次都「Compiled successfully」，lint 无错。

---

## 6. 验收清单（VPS 上 dashboard 起来后）

- [ ] `GET /api/coinglass/futures/tradfi-volume-overview?labels_only=1`
      → `data.available_labels` 含 `TradFi` + `Stocks/Commodities/Indices`，pair_count>0。
- [ ] `GET /api/coinglass/futures/tradfi-volume-overview?interval=1d&label=TradFi`
      → `totals.success_count>0`，`symbols`/`exchanges` 非空。
- [ ] `/dashboard/tradfi` 页默认（TradFi）与切 Stocks/Commodities/Indices 均有图。
- [ ] 数据源：`public/json/tradfi-price/` 有 `{Exchange}_{inst}_{interval}.json`，含 `sector` meta。
- [ ] coinglass 加密面板不受影响（BTC/QNT/HMSTR 等仍在 `futures-price-history`）。

---

## 7. 关键改动摘要（一页速览）

- coinglass：**停 tradfi**（runner 过滤 + 存量清理 13862 个，残留 0；QNT/DIA 判为加密受保护）。
- tradfi：native 独占，独立目录 `tradfi-price`；`copy_to_dashboard` 加 tradfi 分支镜像；socks5h 代理支持。
- dashboard：`route.ts` 改读 `tradfi-price` + native `sector` meta；标签=TradFi+三 sector。
- 待办（非本机）：代理下载 → copy → rsync → 出镜像 → VPS 部署验收。
