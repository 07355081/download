#!/usr/bin/env python3
"""月度大市值山寨相对比特币。

样本是 CoinGecko 当前市值前 100，去掉比特币和稳定币。
价格用 DefiLlama 在每月 1 日 0 点（UTC）的价。
更早的月份用同一份今天的名单回算，不是当时的前 100。
"""
from __future__ import annotations

import json
import os
import statistics
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "output" / "json"
CACHE_PATH = ROOT / "output" / "cache" / "boundary_prices.json"
ENV_PATH = ROOT.parent / ".env"

GECKO = "https://api.coingecko.com/api/v3"
LLAMA = "https://coins.llama.fi/prices/historical"
LOOKBACK_MONTHS = 18
# 每日任务只重算这 18 个已结束月，并保留回补写进文件里的更早月份。
# 更早月份由 backfill.py 走一次，不放进每日任务。
CHUNK = 25
MIN_PRICED = 40
USER_AGENT = "wublock-data-alt-largecap/1.0"

# 类别接口失败时的兜底。只按代码精确匹配，避免把名字里带 usd 的币误删。
STABLE_SYMBOLS = {
    "USDT",
    "USDC",
    "DAI",
    "FDUSD",
    "USDE",
    "TUSD",
    "USDS",
    "PYUSD",
    "USD1",
    "USDD",
    "FRAX",
    "LUSD",
    "GUSD",
    "CRVUSD",
    "USDP",
    "GHO",
    "USDY",
    "USD0",
    "RLUSD",
    "USDG",
    "SUSDS",
    "SUSDE",
    "USDB",
    "BFUSD",
}


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def http_json(url: str, headers: dict[str, str] | None = None, attempts: int = 4) -> object:
    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last = exc
            if attempt < attempts:
                time.sleep(min(2 ** attempt, 12))
    raise RuntimeError(f"GET failed: {last}")


def gecko_key() -> str:
    return (os.environ.get("COINGECKO_DEMO_API_KEY") or os.environ.get("COINGECKO_KEY") or "").strip()


def gecko_get(path: str) -> object:
    headers = {"x-cg-demo-api-key": gecko_key()} if gecko_key() else None
    return http_json(f"{GECKO}{path}", headers=headers)


def add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def day_stamp(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def shift_day(day: str, offset: int) -> str:
    stamp = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    return datetime.fromtimestamp(stamp + offset * 86400, timezone.utc).date().isoformat()


def complete_months(today: datetime) -> list[tuple[str, str, str]]:
    """最近 LOOKBACK_MONTHS 个已经结束的 UTC 月。(月份, 月初, 下月月初)。"""
    end_y, end_m = add_months(today.year, today.month, -1)
    start_y, start_m = add_months(end_y, end_m, -(LOOKBACK_MONTHS - 1))
    out: list[tuple[str, str, str]] = []
    year, month = start_y, start_m
    for _ in range(LOOKBACK_MONTHS):
        next_y, next_m = add_months(year, month, 1)
        label = f"{year:04d}-{month:02d}"
        start = f"{year:04d}-{month:02d}-01"
        end = f"{next_y:04d}-{next_m:02d}-01"
        out.append((label, start, end))
        year, month = next_y, next_m
    return out


def load_cache() -> dict[str, float]:
    if not CACHE_PATH.is_file():
        return {}
    try:
        raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    prices = raw.get("prices") if isinstance(raw, dict) else None
    if not isinstance(prices, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in prices.items():
        if isinstance(key, str) and isinstance(value, (int, float)) and value > 0:
            out[key] = float(value)
    return out


def save_cache(prices: dict[str, float]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "prices": prices}
    tmp = CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_PATH)


def fetch_prices(day: str, ids: list[str]) -> dict[str, float]:
    found: dict[str, float] = {}
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start : start + CHUNK]
        coins = ",".join(f"coingecko:{coin_id}" for coin_id in chunk)
        url = f"{LLAMA}/{day_stamp(day)}/{coins}"
        print(f"    llama {day} x{len(chunk)}")
        payload = http_json(url)
        rows = payload.get("coins") if isinstance(payload, dict) else None
        if not isinstance(rows, dict):
            time.sleep(0.2)
            continue
        for key, row in rows.items():
            if not isinstance(row, dict):
                continue
            price = row.get("price")
            coin_id = str(key).split(":", 1)[-1]
            if isinstance(price, (int, float)) and price > 0:
                found[coin_id] = float(price)
        time.sleep(0.2)
    return found


def price_on(cache: dict[str, float], coin_id: str, day: str) -> float | None:
    for offset in range(0, 3):
        value = cache.get(f"{coin_id}|{shift_day(day, -offset)}")
        if value and value > 0:
            return value
    return None


def ensure_prices(cache: dict[str, float], days: list[str], ids: list[str]) -> None:
    dirty = False
    for day in days:
        missing = [coin_id for coin_id in ids if f"{coin_id}|{day}" not in cache]
        if not missing:
            continue
        found = fetch_prices(day, missing)
        for coin_id in missing:
            price = found.get(coin_id)
            if price:
                cache[f"{coin_id}|{day}"] = price
                dirty = True
        still = [coin_id for coin_id in missing if f"{coin_id}|{day}" not in cache]
        for offset in (1, 2):
            if not still:
                break
            earlier = shift_day(day, -offset)
            found_earlier = fetch_prices(earlier, still)
            for coin_id in list(still):
                price = found_earlier.get(coin_id)
                if price:
                    cache[f"{coin_id}|{earlier}"] = price
                    dirty = True
                    still.remove(coin_id)
        if dirty:
            save_cache(cache)
            dirty = False


def round1(value: float) -> float:
    return round(value, 1)


def build_universe() -> list[dict[str, object]]:
    markets = gecko_get(
        "/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=100&page=1&sparkline=false"
    )
    time.sleep(2.2)
    stables_raw = gecko_get(
        "/coins/markets?vs_currency=usd&category=stablecoins&order=market_cap_desc&per_page=250&page=1&sparkline=false"
    )
    if not isinstance(markets, list):
        raise RuntimeError("CoinGecko 市值榜不是列表")
    stable_ids: set[str] = set()
    if isinstance(stables_raw, list):
        for row in stables_raw:
            if isinstance(row, dict) and row.get("id"):
                stable_ids.add(str(row["id"]))
    universe: list[dict[str, object]] = []
    for row in markets:
        if not isinstance(row, dict):
            continue
        coin_id = str(row.get("id") or "")
        symbol = str(row.get("symbol") or "").upper()
        if not coin_id or coin_id == "bitcoin":
            continue
        if coin_id in stable_ids or symbol in STABLE_SYMBOLS:
            continue
        universe.append(
            {
                "id": coin_id,
                "symbol": symbol,
                "name": str(row.get("name") or symbol),
                "rank": row.get("market_cap_rank"),
            }
        )
    if len(universe) < MIN_PRICED:
        raise RuntimeError(f"去掉比特币和稳定币后只剩 {len(universe)} 个，不写文件")
    return universe


def month_stats(
    cache: dict[str, float],
    universe: list[dict[str, object]],
    label: str,
    start: str,
    end: str,
) -> dict[str, object] | None:
    btc_start = price_on(cache, "bitcoin", start)
    btc_end = price_on(cache, "bitcoin", end)
    if not btc_start or not btc_end:
        print(f"  {label} 比特币价格缺失，跳过")
        return None
    btc_pct = (btc_end / btc_start - 1) * 100
    returns: list[tuple[dict[str, object], float]] = []
    for coin in universe:
        coin_id = str(coin["id"])
        p0 = price_on(cache, coin_id, start)
        p1 = price_on(cache, coin_id, end)
        if not p0 or not p1:
            continue
        returns.append((coin, (p1 / p0 - 1) * 100))
    if len(returns) < MIN_PRICED:
        print(f"  {label} 只有 {len(returns)} 个有价格，跳过")
        return None
    pcts = [pct for _, pct in returns]
    leaders = sorted(returns, key=lambda item: item[1], reverse=True)[:3]
    return {
        "month": label,
        "start": start,
        "end": end,
        "n": len(universe),
        "n_priced": len(returns),
        "n_up": sum(1 for pct in pcts if pct > 0),
        "n_beat_btc": sum(1 for pct in pcts if pct > btc_pct),
        "btc_pct": round1(btc_pct),
        "avg_pct": round1(statistics.fmean(pcts)),
        "median_pct": round1(statistics.median(pcts)),
        "leaders": [
            {
                "id": coin["id"],
                "symbol": coin["symbol"],
                "name": coin["name"],
                "pct": round1(pct),
            }
            for coin, pct in leaders
        ],
    }


def load_saved_months() -> dict[str, dict[str, object]]:
    path = OUT_DIR / "monthly.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    rows = raw.get("months") if isinstance(raw, dict) else None
    out: dict[str, dict[str, object]] = {}
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("month"), str):
                out[str(row["month"])] = row
    return out


def write_monthly(now: datetime, universe: list[dict[str, object]], rows_by_month: dict[str, dict[str, object]]) -> Path:
    rows = [rows_by_month[key] for key in sorted(rows_by_month)]
    payload = {
        "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "CoinGecko 市值榜 + DefiLlama 日价",
        "note": (
            "样本是抓取时 CoinGecko 市值前 100，去掉比特币和稳定币。"
            "更早月份用同一份名单回算，不是当时的前 100。"
            "涨跌从该月 1 日 0 点到下月 1 日 0 点（UTC）。"
            "未结束的月份不计算。中位数达到 15%，或上涨占比至少 70% 且跑赢占比至少 55% 且中位数至少 8%，才推一条 Lark。"
            "若中位数同时高于此前已回算的各月，按历史阈值警报推送。"
        ),
        "median_notable_pct": 15,
        "up_share_notable": 0.7,
        "beat_share_notable": 0.55,
        "median_floor_pct": 8,
        "constituents": universe,
        "months": rows,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUT_DIR / "monthly.json"
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(dest)
    return dest


def main() -> None:
    load_env(ENV_PATH)
    now = datetime.now(timezone.utc)
    universe = build_universe()
    months = complete_months(now)
    days = sorted({day for _, start, end in months for day in (start, end)})
    ids = ["bitcoin", *[str(coin["id"]) for coin in universe]]
    cache = load_cache()
    print(f"样本 {len(universe)} 个，边界日 {len(days)} 个")
    ensure_prices(cache, days, ids)
    saved = load_saved_months()
    for label, start, end in months:
        row = month_stats(cache, universe, label, start, end)
        if row:
            saved[label] = row
            print(
                f"  {label} n={row['n_priced']} up={row['n_up']} beat={row['n_beat_btc']} "
                f"btc={row['btc_pct']} avg={row['avg_pct']} med={row['median_pct']} "
                f"top={','.join(str(item['symbol']) for item in row['leaders'])}"
            )
    if len(saved) < 6:
        raise RuntimeError(f"完整月只有 {len(saved)} 个，不覆盖旧文件")
    dest = write_monthly(now, universe, saved)
    print(f"wrote {dest} months={len(saved)} coins={len(universe)}")


if __name__ == "__main__":
    main()
