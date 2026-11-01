#!/usr/bin/env python3
"""自 BTC ATH 起，当前市值前 ~50 山寨相对 BTC 表现（近似 Glassnode 叙事）。

口径（近似，不是 Glassnode 原图）：
  - 窗口：BTC 全历史最高收盘价所在 UTC 日 → 今日（上海自然日对应的 UTC 日价）
  - 名单：抓取时 CoinGecko 市值前 100 去掉 BTC/稳定币后取前 50（不是 ATH 当日冻结）
  - 相对倍数 rel = (P_alt_end/P_alt_start) / (P_btc_end/P_btc_start)
  - 跑赢：rel > 1

价格：DefiLlama coins.llama.fi/prices/historical
BTC ATH 日：Binance BTCUSDT 日 K 最高收盘（可 ALT50_BTC_ATH_DAY 覆盖）

输出：
  output/json/alt_top50_btc_ath_latest.json
  output/json/alt_top50_btc_ath_history/YYYY-MM-DD.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "output" / "json"
LATEST_PATH = OUT_DIR / "alt_top50_btc_ath_latest.json"
HISTORY_DIR = OUT_DIR / "alt_top50_btc_ath_history"
CACHE_PATH = ROOT / "output" / "cache" / "boundary_prices.json"
ENV_PATH = ROOT.parent / ".env"

GECKO = "https://api.coingecko.com/api/v3"
LLAMA = "https://coins.llama.fi/prices/historical"
BINANCE_KLINES = "https://api.binance.com/api/v3/klines"

TARGET_N = 50
MIN_PRICED = 30
CHUNK = 25
USER_AGENT = "wublock-data-alt-top50-since-btc-ath/1.0"
TZ_CN = timezone(timedelta(hours=8))

STABLE_SYMBOLS = {
    "USDT", "USDC", "DAI", "FDUSD", "USDE", "TUSD", "USDS", "PYUSD", "USD1", "USDD",
    "FRAX", "LUSD", "GUSD", "CRVUSD", "USDP", "GHO", "USDY", "USD0", "RLUSD", "USDG",
    "SUSDS", "SUSDE", "USDB", "BFUSD",
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


def env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def shanghai_day() -> str:
    return datetime.now(TZ_CN).date().isoformat()


def http_json(url: str, headers: dict[str, str] | None = None, attempts: int = 4) -> Any:
    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last = exc
            if attempt < attempts:
                time.sleep(min(2 ** attempt, 12))
    raise RuntimeError(f"GET failed: {last}")


def gecko_key() -> str:
    return (os.environ.get("COINGECKO_DEMO_API_KEY") or os.environ.get("COINGECKO_KEY") or "").strip()


def gecko_get(path: str) -> Any:
    headers = {"x-cg-demo-api-key": gecko_key()} if gecko_key() else None
    return http_json(f"{GECKO}{path}", headers=headers)


def day_stamp(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def shift_day(day: str, offset: int) -> str:
    stamp = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    return datetime.fromtimestamp(stamp + offset * 86400, timezone.utc).date().isoformat()


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
    payload = {"updated_at": now_utc_iso(), "prices": prices}
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


def build_universe(target_n: int) -> list[dict[str, Any]]:
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
    universe: list[dict[str, Any]] = []
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
        if len(universe) >= target_n:
            break
    if len(universe) < min(target_n, MIN_PRICED):
        raise RuntimeError(f"去掉比特币和稳定币后只剩 {len(universe)} 个，不写文件")
    return universe


def fetch_binance_daily_closes() -> list[tuple[str, float]]:
    """拉取尽量长的 BTCUSDT 日收盘（分段 limit=1000）。"""
    out: list[tuple[str, float]] = []
    # 约 2017-08 Binance BTC 上市
    start_ms = int(datetime(2017, 8, 17, tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    while start_ms < end_ms:
        url = (
            f"{BINANCE_KLINES}?symbol=BTCUSDT&interval=1d&limit=1000&startTime={start_ms}"
        )
        print(f"    binance klines from {datetime.fromtimestamp(start_ms/1000, timezone.utc).date()}")
        rows = http_json(url)
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            if not isinstance(row, list) or len(row) < 5:
                continue
            day = datetime.fromtimestamp(int(row[0]) / 1000, timezone.utc).date().isoformat()
            try:
                close = float(row[4])
            except (TypeError, ValueError):
                continue
            if close > 0:
                out.append((day, close))
        last_open = int(rows[-1][0])
        next_start = last_open + 86_400_000
        if next_start <= start_ms:
            break
        start_ms = next_start
        time.sleep(0.15)
        if len(rows) < 1000:
            break
    # 去重保序，同日取后者
    merged: dict[str, float] = {}
    for day, close in out:
        merged[day] = close
    return sorted(merged.items())


def fetch_gecko_daily_closes() -> list[tuple[str, float]]:
    """CoinGecko market_chart days=max（需能出网；演示/正式 Key 均可）。"""
    payload = gecko_get("/coins/bitcoin/market_chart?vs_currency=usd&days=max")
    prices = payload.get("prices") if isinstance(payload, dict) else None
    if not isinstance(prices, list):
        return []
    out: dict[str, float] = {}
    for row in prices:
        if not isinstance(row, list) or len(row) < 2:
            continue
        try:
            ts_ms = float(row[0])
            price = float(row[1])
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        day = datetime.fromtimestamp(ts_ms / 1000.0, timezone.utc).date().isoformat()
        out[day] = price
    return sorted(out.items())


def resolve_btc_ath_day() -> tuple[str, float, str]:
    override = (os.environ.get("ALT50_BTC_ATH_DAY") or "").strip()
    series: list[tuple[str, float]] = []
    source = ""
    try:
        series = fetch_binance_daily_closes()
        source = "binance_btcusdt_daily_close"
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] Binance ATH 失败: {exc}")
    if not series:
        try:
            series = fetch_gecko_daily_closes()
            source = "coingecko_bitcoin_market_chart"
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] CoinGecko ATH 失败: {exc}")
    if not series and not override:
        raise RuntimeError(
            "无法解析 BTC ATH 日（Binance/CoinGecko 均失败）。"
            "可设环境变量 ALT50_BTC_ATH_DAY=YYYY-MM-DD 后重试"
        )
    if override:
        price = next((c for d, c in series if d == override), None) if series else None
        if price:
            return override, price, f"{source or 'override'}+override_day"
        # 无序列时仍接受覆盖日，价留给 Llama 边界拉取
        if not series:
            print(f"  [warn] 仅使用 ALT50_BTC_ATH_DAY={override}，收盘提示价未知")
            return override, 0.0, "env_override_only"
        print(f"  [warn] ALT50_BTC_ATH_DAY={override} 无对应收盘，回退序列最高日")
    best_day, best_close = max(series, key=lambda item: item[1])
    return best_day, best_close, source


def round2(value: float) -> float:
    return round(value, 2)


def round3(value: float) -> float:
    return round(value, 3)


def window_stats(
    cache: dict[str, float],
    universe: list[dict[str, Any]],
    start: str,
    end: str,
) -> dict[str, Any]:
    btc_start = price_on(cache, "bitcoin", start)
    btc_end = price_on(cache, "bitcoin", end)
    if not btc_start or not btc_end:
        raise RuntimeError(f"比特币边界价缺失 start={start} end={end}")
    btc_mult = btc_end / btc_start
    btc_pct = (btc_mult - 1.0) * 100.0
    if btc_mult <= 0:
        raise RuntimeError("比特币倍数异常")

    rows: list[dict[str, Any]] = []
    for coin in universe:
        coin_id = str(coin["id"])
        p0 = price_on(cache, coin_id, start)
        p1 = price_on(cache, coin_id, end)
        if not p0 or not p1:
            continue
        alt_mult = p1 / p0
        rel = alt_mult / btc_mult
        alt_pct = (alt_mult - 1.0) * 100.0
        rows.append(
            {
                "id": coin_id,
                "symbol": coin["symbol"],
                "name": coin["name"],
                "rank": coin.get("rank"),
                "start_price": round2(p0),
                "end_price": round2(p1),
                "alt_pct": round2(alt_pct),
                "rel": round3(rel),
                "beat_btc": rel > 1.0,
            }
        )

    if len(rows) < MIN_PRICED:
        raise RuntimeError(f"只有 {len(rows)} 个有完整边界价，低于 {MIN_PRICED}")

    rels = [r["rel"] for r in rows]
    beat = [r for r in rows if r["beat_btc"]]
    leaders = sorted(rows, key=lambda r: r["rel"], reverse=True)[:10]
    return {
        "n": len(universe),
        "n_priced": len(rows),
        "n_beat_btc": len(beat),
        "beat_share": round3(len(beat) / len(rows)),
        "btc_start_price": round2(btc_start),
        "btc_end_price": round2(btc_end),
        "btc_pct": round2(btc_pct),
        "btc_mult": round3(btc_mult),
        "median_rel": round3(statistics.median(rels)),
        "avg_rel": round3(statistics.fmean(rels)),
        "leaders": [
            {
                "id": r["id"],
                "symbol": r["symbol"],
                "name": r["name"],
                "rel": r["rel"],
                "alt_pct": r["alt_pct"],
            }
            for r in leaders
        ],
        "constituents_priced": rows,
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


SERIES_PATH = OUT_DIR / "alt_top50_btc_ath_series.json"


def write_series() -> int:
    """把已落盘的日文件收成一份序列。只读本地文件，不另发请求。"""
    points: list[dict[str, Any]] = []
    if HISTORY_DIR.is_dir():
        for path in sorted(HISTORY_DIR.glob("????-??-??.json")):
            try:
                snap = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(snap, dict):
                continue
            share = snap.get("beat_share")
            if not isinstance(share, (int, float)):
                continue
            median = snap.get("median_rel")
            points.append(
                {
                    "day": str(snap.get("day") or path.stem),
                    "beat_share": float(share),
                    "median_rel": float(median) if isinstance(median, (int, float)) else None,
                }
            )
    write_json(
        SERIES_PATH,
        {
            "code": "0",
            "name": "alt_top50_btc_ath_series",
            "updated_at": now_utc_iso(),
            "points": points,
        },
    )
    return len(points)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_env(ENV_PATH)
    target_n = env_int("ALT50_TARGET_N", TARGET_N)
    day = shanghai_day()

    print("[alt-top50-since-btc-ath] universe + BTC ATH window")
    if args.dry_run:
        print("DRY-RUN: 将请求 CoinGecko / Binance / DefiLlama，不写盘")
        return 0

    started = time.time()
    universe = build_universe(target_n)
    ath_day, ath_close_hint, ath_source = resolve_btc_ath_day()
    end_day = day  # 上海日；Llama 用该 UTC 日 0 点价（与 alt-largecap 一致）
    print(f"  ath_day={ath_day} binance_close≈{ath_close_hint:.2f} source={ath_source}")
    print(f"  end_day={end_day} universe={len(universe)}")

    ids = ["bitcoin", *[str(c["id"]) for c in universe]]
    cache = load_cache()
    ensure_prices(cache, [ath_day, end_day], ids)
    stats = window_stats(cache, universe, ath_day, end_day)

    payload: dict[str, Any] = {
        "code": "0",
        "msg": "success",
        "name": "alt_top50_btc_ath",
        "description": "当前市值前约50山寨自BTC ATH起相对BTC表现（近似）",
        "source": "CoinGecko markets + Binance BTC ATH day + DefiLlama historical prices",
        "methodology": {
            "universe": (
                f"CoinGecko market_cap_desc top100 minus bitcoin/stables, take first {target_n}; "
                "snapshot at fetch time (NOT frozen on ATH day)"
            ),
            "window": "BTC ATH UTC day (Binance BTCUSDT daily close max) → Shanghai calendar day price",
            "relative_multiple": "rel = (P_alt_end/P_alt_start) / (P_btc_end/P_btc_start); beat if rel>1",
            "note": (
                "Approximates Glassnode 'alts vs BTC since ATH' narrative; "
                "membership differs from ATH-day frozen top50."
            ),
        },
        "updated_at": now_utc_iso(),
        "day": day,
        "ath_day": ath_day,
        "ath_source": ath_source,
        "ath_binance_close_hint": round2(ath_close_hint),
        "end_day": end_day,
        "window_days": (
            datetime.strptime(end_day, "%Y-%m-%d") - datetime.strptime(ath_day, "%Y-%m-%d")
        ).days,
        "target_n": target_n,
        "constituents": universe,
        **stats,
        "thresholds": {
            "beat_share_line": float(os.environ.get("ALT50_BEAT_SHARE_LINE") or 0.5),
            "median_rel_line": float(os.environ.get("ALT50_REL_MEDIAN_LINE") or 1.0),
        },
    }

    write_json(LATEST_PATH, payload)
    hist = HISTORY_DIR / f"{day}.json"
    write_json(hist, payload)
    series_n = write_series()

    top = "、".join(f"{r['symbol']} {r['rel']}x" for r in stats["leaders"][:5]) or "—"
    print(
        f"    day={day} priced={stats['n_priced']} beat={stats['n_beat_btc']}/"
        f"{stats['n_priced']} med_rel={stats['median_rel']} "
        f"btc={stats['btc_pct']}% top=[{top}] series={series_n} {time.time()-started:.1f}s"
    )
    print(f"json -> {LATEST_PATH}")
    print(f"hist -> {hist}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
