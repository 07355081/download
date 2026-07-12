"""按 CoinGecko 分类 CSV + OKX/Gate/MEXC 原生标签，为 Coinglass base_asset 打标。

CoinGecko 撞名：先拉全局市值 Top ~5000 建 symbol→权威 coin id，分类扫描只认权威 id；
无权威记录的 symbol 则同名取最大市值。

流程：
  1. python download_coingecko_categories.py   # 生成分类 CSV，再手动删不需要的行
  2. python classify_coinglass_instruments.py  # 打标 → CSV + summary JSON
  3. python copy_to_dashboard.py --only tag    # 同步 JSON 到 dashboard

输入：
  coinglass-history/symbols/{futures,spot}_instruments.json
  output/csv/coingecko_categories_by_market_cap.csv  （CoinGecko 白名单）

输出：
  output/csv/base_asset_categories.csv
  output/json/base_asset_categories_summary.json

可选参数：
  --only coingecko|okx,gate,mexc   只更新部分列（默认全部）
  --coingecko-categories id1,id2   只补拉指定 CoinGecko 分类并合并
  --list-coingecko-categories      打印白名单 CSV 中的分类
  --no-summarize                   只写 CSV，不生成 summary JSON
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
COINGLASS_SYMBOLS = ROOT.parent / "coinglass-history" / "symbols"
COINGECKO_CATALOG_CSV = ROOT / "output" / "csv" / "coingecko_categories_by_market_cap.csv"
OUT_CSV = ROOT / "output" / "csv" / "base_asset_categories.csv"
OUT_SUMMARY_JSON = ROOT / "output" / "json" / "base_asset_categories_summary.json"

UA = "coinglass-native-classifier/2.0"
GECKO_BASE = "https://api.coingecko.com/api/v3"
GECKO_PAGE_SLEEP_S = 1.5
GECKO_CATEGORY_SLEEP_S = 1.0
GECKO_RETRIES = 10
GECKO_429_BASE_SLEEP_S = 12.0
GECKO_429_MAX_SLEEP_S = 90.0
GECKO_PAGE_RETRIES = 4
GECKO_GLOBAL_AUTHORITY_PAGES = 20  # top ~5000 by market cap
GECKO_MARKETS_PER_PAGE = 250

OKX_INST_CATEGORY = {
    "1": "Crypto", "3": "Stocks", "4": "Commodities",
    "5": "Forex", "6": "Bonds", "": "N/A",
}

# 输出列（base_asset + 4 个分类列）
COL_BASE = "base_asset"
COL_GECKO = "CoinGecko_categories"
COL_OKX = "OKX"
COL_GATE = "Gate"
COL_MEXC = "MEXC"
OUTPUT_COLUMNS = (COL_BASE, COL_GECKO, COL_OKX, COL_GATE, COL_MEXC)

SOURCE_COINGECKO = "coingecko"
SOURCE_OKX = "OKX"
SOURCE_GATE = "Gate"
SOURCE_MEXC = "MEXC"
ALL_SOURCES = frozenset({SOURCE_COINGECKO, SOURCE_OKX, SOURCE_GATE, SOURCE_MEXC})

SOURCE_ALIASES = {
    "coingecko": SOURCE_COINGECKO, "gecko": SOURCE_COINGECKO, "cg": SOURCE_COINGECKO,
    "okx": SOURCE_OKX, "gate": SOURCE_GATE, "mexc": SOURCE_MEXC,
    "exchanges": "__exchanges__", "all": "__all__",
}


# ── HTTP ─────────────────────────────────────────────────────────


def get_json(url: str, *, headers: dict[str, str] | None = None, params: dict[str, str] | None = None,
             timeout: int = 60, retries: int = 3) -> Any:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429 and attempt + 1 < retries:
                time.sleep(1.5 + attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            if attempt + 1 < retries:
                time.sleep(1.0 + attempt)
                continue
            raise
    raise last_err  # type: ignore[misc]


def _sleep_on_gecko_429(err: urllib.error.HTTPError, attempt: int) -> None:
    retry_after = err.headers.get("Retry-After") if err.headers else None
    try:
        wait = float(retry_after) if retry_after else GECKO_429_BASE_SLEEP_S * (2 ** attempt)
    except ValueError:
        wait = GECKO_429_BASE_SLEEP_S * (2 ** attempt)
    wait = min(max(wait, GECKO_429_BASE_SLEEP_S), GECKO_429_MAX_SLEEP_S)
    print(f"  rate limited (429), waiting {wait:.0f}s before retry ...", flush=True)
    time.sleep(wait)


def get_gecko_json(url: str, *, params: dict[str, str] | None = None, timeout: int = 60) -> Any:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    last_err: Exception | None = None
    for attempt in range(GECKO_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429 and attempt + 1 < GECKO_RETRIES:
                _sleep_on_gecko_429(e, attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            if attempt + 1 < GECKO_RETRIES:
                time.sleep(2.0 + attempt)
                continue
            raise
    raise last_err  # type: ignore[misc]


def parse_market_cap(value: Any) -> float:
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if parsed > 0 else 0.0
    return 0.0


def fetch_coingecko_markets_page(*, page: int, category: str | None = None) -> list[dict[str, Any]]:
    label = category or "global"
    last_err: Exception | None = None
    params: dict[str, str] = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": str(GECKO_MARKETS_PER_PAGE),
        "page": str(page),
        "sparkline": "false",
    }
    if category:
        params["category"] = category
    for round_idx in range(GECKO_PAGE_RETRIES):
        try:
            markets = get_gecko_json(f"{GECKO_BASE}/coins/markets", params=params)
            if not isinstance(markets, list):
                raise RuntimeError(f"unexpected response: {type(markets).__name__}")
            return markets
        except Exception as e:
            last_err = e
            if round_idx + 1 < GECKO_PAGE_RETRIES:
                wait = GECKO_429_BASE_SLEEP_S * (round_idx + 1)
                print(f"  WARN: {label} page {page} failed; retry in {wait:.0f}s", flush=True)
                time.sleep(wait)
                continue
            raise last_err
    raise last_err  # type: ignore[misc]


def build_global_symbol_authority(*, max_pages: int = GECKO_GLOBAL_AUTHORITY_PAGES) -> dict[str, str]:
    """symbol(upper) -> CoinGecko coin id with highest global market cap."""
    authority: dict[str, str] = {}
    best_mcap: dict[str, float] = {}
    print(f"building global symbol authority (top {max_pages * GECKO_MARKETS_PER_PAGE} by market cap)...", flush=True)
    for page in range(1, max_pages + 1):
        try:
            markets = fetch_coingecko_markets_page(page=page)
        except Exception as e:
            print(f"  WARN: global markets page {page} failed: {e}", flush=True)
            break
        if not markets:
            break
        for coin in markets:
            if not isinstance(coin, dict):
                continue
            sym = str(coin.get("symbol") or "").upper().strip()
            coin_id = str(coin.get("id") or "").strip()
            if not sym or not coin_id:
                continue
            mcap = parse_market_cap(coin.get("market_cap"))
            if mcap >= best_mcap.get(sym, -1.0):
                best_mcap[sym] = mcap
                authority[sym] = coin_id
        if len(markets) < GECKO_MARKETS_PER_PAGE:
            break
        if page % 5 == 0:
            print(f"  global authority scanned: {page}/{max_pages} pages", flush=True)
        time.sleep(GECKO_PAGE_SLEEP_S)
    print(f"  global authority: {len(authority)} symbols", flush=True)
    return authority


def _coin_cats_add(
    store: dict[str, dict[str, tuple[float, set[str]]]],
    sym: str,
    coin_id: str,
    mcap: float,
    cat_name: str,
) -> None:
    by_id = store.setdefault(sym, {})
    prev_mcap, prev_cats = by_id.get(coin_id, (0.0, set()))
    by_id[coin_id] = (max(prev_mcap, mcap), prev_cats | {cat_name})


def resolve_symbol_categories(
    symbol_coin_cats: dict[str, dict[str, tuple[float, set[str]]]],
    authority: dict[str, str],
) -> dict[str, set[str]]:
    """Authority id wins when known; otherwise pick highest market cap among candidates."""
    result: dict[str, set[str]] = {}
    for sym, by_id in symbol_coin_cats.items():
        if not by_id:
            continue
        auth_id = authority.get(sym)
        if auth_id:
            hit = by_id.get(auth_id)
            if hit:
                result[sym] = set(hit[1])
            continue
        best_id = max(by_id.keys(), key=lambda cid: by_id[cid][0])
        result[sym] = set(by_id[best_id][1])
    return result


# ── 数据加载 ─────────────────────────────────────────────────────


def load_coinglass_instruments() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for market in ("futures", "spot"):
        path = COINGLASS_SYMBOLS / f"{market}_instruments.json"
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        for exchange, pairs in (doc.get("by_exchange") or {}).items():
            for p in pairs:
                if isinstance(p, dict):
                    rows.append({
                        "market": market,
                        "exchange": exchange,
                        "instrument_id": p.get("instrument_id") or "",
                        "base_asset": p.get("base_asset") or "",
                        "quote_asset": p.get("quote_asset") or "",
                    })
    return rows


def load_coingecko_catalog() -> list[tuple[str, str]]:
    if not COINGECKO_CATALOG_CSV.is_file():
        raise SystemExit(
            f"未找到 CoinGecko 分类清单: {COINGECKO_CATALOG_CSV}\n"
            "  请先运行: python download_coingecko_categories.py"
        )
    catalog: list[tuple[str, str]] = []
    with COINGECKO_CATALOG_CSV.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            cid = str(row.get("id") or "").strip()
            name = str(row.get("name") or "").strip()
            if cid and name:
                catalog.append((cid, name))
    if not catalog:
        raise SystemExit(f"分类清单为空: {COINGECKO_CATALOG_CSV}")
    return catalog


def load_existing_rows(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        out: dict[str, dict[str, str]] = {}
        for row in reader:
            base = str(row.get(COL_BASE) or "").strip()
            if base:
                out[base] = {col: str(row.get(col) or "") for col in OUTPUT_COLUMNS}
        return out


def parse_sources(raw: str | None) -> frozenset[str]:
    if not raw:
        return ALL_SOURCES
    selected: set[str] = set()
    for part in raw.split(","):
        token = part.strip().lower()
        if not token:
            continue
        norm = SOURCE_ALIASES.get(token)
        if norm == "__all__":
            return ALL_SOURCES
        if norm == "__exchanges__":
            selected.update({SOURCE_OKX, SOURCE_GATE, SOURCE_MEXC})
        elif norm:
            selected.add(norm)
        else:
            raise SystemExit(f"未知 source: {part!r}，可用: coingecko, okx, gate, mexc, exchanges, all")
    return frozenset(selected) if selected else ALL_SOURCES


def resolve_coingecko_tokens(tokens: list[str], catalog: list[tuple[str, str]]) -> list[tuple[str, str]]:
    by_id = {cid.lower(): (cid, name) for cid, name in catalog}
    by_name = {name.lower(): (cid, name) for cid, name in catalog}
    resolved: list[tuple[str, str]] = []
    seen: set[str] = set()
    for token in tokens:
        t = token.strip()
        if not t:
            continue
        tl = t.lower()
        hit = by_id.get(tl) or by_name.get(tl)
        if not hit:
            for cid, name in catalog:
                if tl in cid.lower() or tl in name.lower():
                    hit = (cid, name)
                    break
        if not hit:
            raise SystemExit(f"未知 CoinGecko 分类 {token!r}，用 --list-coingecko-categories 查看")
        if hit[0] not in seen:
            seen.add(hit[0])
            resolved.append(hit)
    return resolved


def columns_for_sources(sources: frozenset[str]) -> list[str]:
    cols: list[str] = []
    if SOURCE_COINGECKO in sources:
        cols.append(COL_GECKO)
    if SOURCE_OKX in sources:
        cols.append(COL_OKX)
    if SOURCE_GATE in sources:
        cols.append(COL_GATE)
    if SOURCE_MEXC in sources:
        cols.append(COL_MEXC)
    return cols


def merge_rows(
    instruments: list[dict[str, str]],
    partial: dict[str, dict[str, str]],
    existing: dict[str, dict[str, str]],
    updated_cols: list[str],
) -> list[dict[str, str]]:
    bases = sorted({i["base_asset"] for i in instruments if i["base_asset"]})
    rows: list[dict[str, str]] = []
    for base in bases:
        row = {col: "" for col in OUTPUT_COLUMNS}
        row[COL_BASE] = base
        prev = existing.get(base)
        if prev:
            for col in OUTPUT_COLUMNS:
                row[col] = prev.get(col, "")
        patch = partial.get(base)
        if patch:
            for col in updated_cols:
                row[col] = patch.get(col, "")
        rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(OUTPUT_COLUMNS))
        w.writeheader()
        w.writerows(rows)
    try:
        tmp.replace(path)
    except PermissionError as e:
        raise SystemExit(
            f"无法写入 {path}（文件可能被 Excel 占用），请关闭后重试\n"
            f"临时文件: {tmp}"
        ) from e


def agg_values(values: list[str]) -> str:
    parts: set[str] = set()
    for v in values:
        for p in (v or "").split("|"):
            p = p.strip()
            if p and p != "N/A":
                parts.add(p)
    return "|".join(sorted(parts))


# ── CoinGecko 打标 ───────────────────────────────────────────────


def scan_coingecko_categories(
    categories: list[tuple[str, str]],
    authority: dict[str, str] | None = None,
) -> tuple[dict[str, set[str]], list[tuple[str, str, str, str]]]:
    symbol_coin_cats: dict[str, dict[str, tuple[float, set[str]]]] = {}
    auth = authority or {}
    skipped_imposters = 0
    not_fetched: list[tuple[str, str, str]] = []
    partial_fetched: list[tuple[str, str, str]] = []

    for idx, (cat_id, cat_name) in enumerate(categories, start=1):
        page = 1
        got_coins = False
        while True:
            try:
                markets = fetch_coingecko_markets_page(page=page, category=cat_id)
            except Exception as e:
                print(f"  WARN: skip {cat_id} ({cat_name}): {e}", flush=True)
                (not_fetched if not got_coins else partial_fetched).append(
                    (cat_id, cat_name, str(e) if not got_coins else f"page {page}: {e}")
                )
                break
            if not markets:
                if not got_coins:
                    not_fetched.append((cat_id, cat_name, "empty response"))
                break
            for coin in markets:
                if not isinstance(coin, dict):
                    continue
                sym = str(coin.get("symbol") or "").upper().strip()
                coin_id = str(coin.get("id") or "").strip()
                if not sym or not coin_id:
                    continue
                if sym in auth and coin_id != auth[sym]:
                    skipped_imposters += 1
                    continue
                mcap = parse_market_cap(coin.get("market_cap"))
                _coin_cats_add(symbol_coin_cats, sym, coin_id, mcap, cat_name)
                got_coins = True
            if len(markets) < GECKO_MARKETS_PER_PAGE:
                break
            page += 1
            time.sleep(GECKO_PAGE_SLEEP_S)
        if len(categories) > 1 and (idx % 5 == 0 or idx == len(categories)):
            print(f"  coingecko scanned: {idx}/{len(categories)}", flush=True)
        time.sleep(GECKO_CATEGORY_SLEEP_S)

    symbol_cats = resolve_symbol_categories(symbol_coin_cats, auth)
    if skipped_imposters:
        print(f"  skipped {skipped_imposters} same-symbol imposter coins (authority filter)", flush=True)

    failed_map = {x[0]: x[2] for x in not_fetched}
    partial_map = {x[0]: x[2] for x in partial_fetched}
    records: list[tuple[str, str, str, str]] = []
    for cat_id, cat_name in categories:
        if cat_id in failed_map:
            records.append((cat_id, cat_name, "failed", failed_map[cat_id]))
        elif cat_id in partial_map:
            records.append((cat_id, cat_name, "partial", partial_map[cat_id]))
        else:
            records.append((cat_id, cat_name, "ok", ""))

    ok_n = sum(1 for *_, st, _ in records if st == "ok")
    print(f"  coingecko summary: {ok_n} OK, {len(partial_fetched)} partial, "
          f"{len(not_fetched)} failed / {len(categories)}", flush=True)
    if not_fetched:
        print(f"  NOT fetched ({len(not_fetched)}):", flush=True)
        for cid, name, reason in not_fetched:
            print(f"    - {cid} ({name}): {reason}", flush=True)
    if partial_fetched:
        print(f"  PARTIAL ({len(partial_fetched)}):", flush=True)
        for cid, name, reason in partial_fetched:
            print(f"    - {cid} ({name}): {reason}", flush=True)
    retry_ids = [cid for cid, _, st, _ in records if st in ("failed", "partial")]
    if retry_ids:
        print(f"  retry: python classify_coinglass_instruments.py "
              f"--coingecko-categories {','.join(retry_ids)}", flush=True)
    return dict(symbol_cats), records


def coingecko_lookup_keys(base_asset: str) -> list[str]:
    b = base_asset.upper().strip()
    if not b:
        return []
    keys = [b]
    m = re.match(r"^(\d+)([A-Z][A-Z0-9]*)$", b)
    if m:
        keys.append(m.group(2))
    if b.endswith("X") and len(b) > 2:
        keys.append(b[:-1])
    return keys


def gecko_tags_for_base(base: str, index: dict[str, set[str]], allowed: set[str]) -> str:
    names: set[str] = set()
    for key in coingecko_lookup_keys(base):
        names.update(index.get(key, ()))
    return "|".join(sorted(n for n in names if n in allowed))


def merge_gecko_column(existing: str, new_tags: set[str], allowed: set[str]) -> str:
    parts = {p.strip() for p in existing.split("|") if p.strip() and p.strip() in allowed}
    parts.update(t for t in new_tags if t in allowed)
    return "|".join(sorted(parts))


# ── 交易所索引（OKX / Gate / MEXC）──────────────────────────────


def build_okx_index() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for inst_type in ("SPOT", "SWAP", "FUTURES"):
        try:
            payload = get_json(f"https://www.okx.com/api/v5/public/instruments?instType={inst_type}")
        except urllib.error.HTTPError:
            continue
        for r in payload.get("data") or []:
            if isinstance(r, dict):
                iid = str(r.get("instId") or "")
                code = str(r.get("instCategory", ""))
                out[iid] = {"instCategory": OKX_INST_CATEGORY.get(code, code) if code else ""}
        time.sleep(0.15)
    return out


def build_gate_index() -> dict[str, str]:
    out: dict[str, str] = {}
    for c in get_json("https://api.gateio.ws/api/v4/spot/currencies"):
        if not isinstance(c, dict):
            continue
        sym = str(c.get("currency") or "").upper()
        cats = c.get("category") or []
        if isinstance(cats, str):
            cats = [cats]
        if cats:
            out[sym] = "|".join(sorted({str(x).strip() for x in cats if str(x).strip()}))
    return out


def build_mexc_index() -> dict[str, str]:
    plate_names: dict[int, str] = {}
    try:
        plates = get_json(
            "https://www.mexc.com/api/platform/spot/market-v2/web/concept/plates",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=30,
        ).get("data") or []
        plate_names = {int(p["id"]): str(p["n"]) for p in plates if isinstance(p, dict) and "id" in p}
    except (urllib.error.HTTPError, urllib.error.URLError, KeyError, ValueError):
        pass
    out: dict[str, str] = {}
    for s in get_json("https://api.mexc.com/api/v3/exchangeInfo", timeout=120).get("symbols") or []:
        if not isinstance(s, dict):
            continue
        names = []
        for raw in s.get("conceptPlateIds") or []:
            try:
                name = plate_names.get(int(raw))
                if name:
                    names.append(name)
            except (TypeError, ValueError):
                continue
        sym = str(s.get("symbol") or "")
        if names:
            out[sym] = "|".join(names)
    return out


def build_exchange_matrix(
    instruments: list[dict[str, str]],
    sources: frozenset[str],
) -> dict[str, dict[str, str]]:
    indexes: dict[str, Any] = {}
    if SOURCE_OKX in sources:
        print("building index for OKX...", flush=True)
        try:
            indexes[SOURCE_OKX] = build_okx_index()
        except Exception as e:
            print(f"  WARN: OKX failed: {e}", flush=True)
            indexes[SOURCE_OKX] = {}
    if SOURCE_GATE in sources:
        print("building index for Gate...", flush=True)
        try:
            indexes[SOURCE_GATE] = build_gate_index()
        except Exception as e:
            print(f"  WARN: Gate failed: {e}", flush=True)
            indexes[SOURCE_GATE] = {}
    if SOURCE_MEXC in sources:
        print("building index for MEXC...", flush=True)
        try:
            indexes[SOURCE_MEXC] = build_mexc_index()
        except Exception as e:
            print(f"  WARN: MEXC failed: {e}", flush=True)
            indexes[SOURCE_MEXC] = {}

    bases = sorted({i["base_asset"] for i in instruments if i["base_asset"]})
    okx_vals: dict[str, list[str]] = defaultdict(list)
    gate_vals: dict[str, list[str]] = defaultdict(list)
    mexc_vals: dict[str, list[str]] = defaultdict(list)

    okx_idx = indexes.get(SOURCE_OKX, {})
    gate_idx = indexes.get(SOURCE_GATE, {})
    mexc_idx = indexes.get(SOURCE_MEXC, {})

    for inst in instruments:
        base = inst["base_asset"]
        if not base:
            continue
        ex = inst["exchange"]
        if ex == SOURCE_OKX and SOURCE_OKX in sources:
            hit = okx_idx.get(inst["instrument_id"]) if isinstance(okx_idx, dict) else None
            if hit and hit.get("instCategory"):
                okx_vals[base].append(hit["instCategory"])
        elif ex == SOURCE_MEXC and SOURCE_MEXC in sources:
            sym = inst["instrument_id"].replace("_", "")
            val = mexc_idx.get(sym, "") if isinstance(mexc_idx, dict) else ""
            if val:
                mexc_vals[base].append(val)

    if SOURCE_GATE in sources and isinstance(gate_idx, dict):
        for base in bases:
            val = gate_idx.get(base.upper(), "")
            if val:
                gate_vals[base].append(val)

    partial: dict[str, dict[str, str]] = {}
    for base in bases:
        row: dict[str, str] = {}
        if SOURCE_OKX in sources:
            row[COL_OKX] = agg_values(okx_vals[base])
        if SOURCE_GATE in sources:
            row[COL_GATE] = agg_values(gate_vals[base])
        if SOURCE_MEXC in sources:
            row[COL_MEXC] = agg_values(mexc_vals[base])
        if row:
            partial[base] = row
    return partial


def build_gecko_partial(
    instruments: list[dict[str, str]],
    index: dict[str, set[str]],
    allowed: set[str],
) -> dict[str, dict[str, str]]:
    bases = sorted({i["base_asset"] for i in instruments if i["base_asset"]})
    return {
        base: {COL_GECKO: gecko_tags_for_base(base, index, allowed)}
        for base in bases
    }


def build_gecko_merge_partial(
    instruments: list[dict[str, str]],
    index: dict[str, set[str]],
    existing: dict[str, dict[str, str]],
    allowed: set[str],
) -> dict[str, dict[str, str]]:
    bases = sorted({i["base_asset"] for i in instruments if i["base_asset"]})
    partial: dict[str, dict[str, str]] = {}
    for base in bases:
        new_tags: set[str] = set()
        for key in coingecko_lookup_keys(base):
            new_tags.update(index.get(key, ()))
        existing_val = str(existing.get(base, {}).get(COL_GECKO) or "")
        partial[base] = {COL_GECKO: merge_gecko_column(existing_val, new_tags, allowed)}
    return partial


# ── CLI ──────────────────────────────────────────────────────────


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Coinglass base_asset 打标（CoinGecko + OKX/Gate/MEXC）")
    p.add_argument("--only", metavar="SOURCES",
                   help="coingecko | okx,gate,mexc | exchanges | all（默认 all）")
    p.add_argument("--coingecko-categories", metavar="CATS",
                   help="只补拉指定 CoinGecko 分类 id 并合并到 CoinGecko 列")
    p.add_argument("--list-coingecko-categories", action="store_true",
                   help="打印白名单 CSV 中的分类")
    p.add_argument("--no-summarize", action="store_true",
                   help="只写 CSV，不生成 base_asset_categories_summary.json")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.list_coingecko_categories:
        catalog = load_coingecko_catalog()
        print(f"Catalog ({COINGECKO_CATALOG_CSV.name}, {len(catalog)}):", flush=True)
        for i, (cid, name) in enumerate(catalog, 1):
            print(f"  {i:2}. {cid}  ({name})", flush=True)
        return 0

    gecko_tokens = [t.strip() for t in (args.coingecko_categories or "").split(",") if t.strip()]
    sources = frozenset({SOURCE_COINGECKO}) if gecko_tokens else parse_sources(args.only)
    updated_cols = columns_for_sources(sources)

    instruments = load_coinglass_instruments()
    if not instruments:
        raise SystemExit(f"未找到 instruments: {COINGLASS_SYMBOLS}")

    existing = load_existing_rows(OUT_CSV)
    partial: dict[str, dict[str, str]] = {}

    if gecko_tokens:
        print("Mode: merge CoinGecko categories", flush=True)
        catalog = load_coingecko_catalog()
        allowed = {name for _, name in catalog}
        selected = resolve_coingecko_tokens(gecko_tokens, catalog)
        for cid, name in selected:
            print(f"  category: {cid} ({name})", flush=True)
        authority = build_global_symbol_authority()
        index, _records = scan_coingecko_categories(selected, authority)
        print(f"  symbols indexed: {len(index)}", flush=True)
        partial.update(build_gecko_merge_partial(instruments, index, existing, allowed))
    else:
        if SOURCE_COINGECKO in sources:
            catalog = load_coingecko_catalog()
            allowed = {name for _, name in catalog}
            authority = build_global_symbol_authority()
            print("building coingecko index...", flush=True)
            index, _records = scan_coingecko_categories(catalog, authority)
            print(f"  symbols indexed: {len(index)}", flush=True)
            partial.update(build_gecko_partial(instruments, index, allowed))

        ex_sources = sources & {SOURCE_OKX, SOURCE_GATE, SOURCE_MEXC}
        if ex_sources:
            partial_ex = build_exchange_matrix(instruments, ex_sources)
            for base, row in partial_ex.items():
                partial.setdefault(base, {}).update(row)

    if not partial:
        raise SystemExit("没有选中任何数据源")

    print("writing CSV...", flush=True)
    rows = merge_rows(instruments, partial, existing, updated_cols)
    write_csv(OUT_CSV, rows)
    filled = {c: sum(1 for r in rows if r.get(c)) for c in OUTPUT_COLUMNS[1:]}
    print(f"Wrote {len(rows)} rows -> {OUT_CSV}", flush=True)
    print("  filled: " + ", ".join(f"{k}={v}" for k, v in filled.items()), flush=True)

    if not args.no_summarize:
        from summarize_base_asset_categories import write_summary_from_rows

        print("writing summary JSON...", flush=True)
        write_summary_from_rows(rows, out_json=OUT_SUMMARY_JSON, source_csv=OUT_CSV)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
