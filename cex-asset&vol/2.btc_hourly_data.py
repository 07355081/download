# -*- coding: utf-8 -*-
"""Download BTC hourly OHLC (Binance BTCUSDT) and build daily VWAP in USD.

Outputs (see cex_pipeline_manifest.csv):
  - btc_hourly_data.csv   columns: time_ms, open, high, low, close, volume_usd
  - btc_daily_vwap.csv    columns: date, vwap_usd

Data sources (in priority order for merge):
  1. Existing btc_hourly_data.csv
  2. coinglass-history/spot-price-history/cache/Binance_BTCUSDT_1h.csv
  3. Coinglass API GET /api/spot/price/history (paginate with end_time)
  4. Daily bars from .../Binance_BTCUSDT_1d.csv for dates without hourly coverage
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
HOURLY_CSV = BASE_DIR / "btc_hourly_data.csv"
DAILY_VWAP_CSV = BASE_DIR / "btc_daily_vwap.csv"

COINGLASS_ROOT = BASE_DIR.parent / "coinglass-history"
SPOT_CACHE_DIR = COINGLASS_ROOT / "spot-price-history" / "cache"
LOCAL_1H_CACHE = SPOT_CACHE_DIR / "Binance_BTCUSDT_1h.csv"
LOCAL_1D_CACHE = SPOT_CACHE_DIR / "Binance_BTCUSDT_1d.csv"

EXCHANGE = "Binance"
SYMBOL = "BTCUSDT"
INTERVAL = "1h"
API_PATH = "/api/spot/price/history"
API_LIMIT = 4500
HOUR_MS = 60 * 60 * 1000
DAY_MS = 24 * HOUR_MS

OHLC_HEADER = ["time_ms", "open", "high", "low", "close", "volume_usd"]

# Align with CoinGecko volume history (step 1) — extend if major_vol goes earlier.
DEFAULT_START_DATE = date(2017, 8, 17)


def _load_coinglass_common():
    common_path = COINGLASS_ROOT / "_common.py"
    if not common_path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("coinglass_common", common_path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (ValueError, TypeError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def typical_price(open_: float, high: float, low: float, close: float) -> float:
    if all(x > 0 for x in (high, low, close)):
        return (high + low + close) / 3.0
    if close > 0:
        return close
    if open_ > 0:
        return open_
    return float("nan")


def read_hourly_csv(path: Path) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    if not path.is_file():
        return out
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts = int(float(row["time_ms"]))
            except (KeyError, ValueError, TypeError):
                continue
            o = _to_float(row.get("open"))
            h = _to_float(row.get("high"))
            l = _to_float(row.get("low"))
            c = _to_float(row.get("close"))
            vol = _to_float(row.get("volume_usd"))
            if c is None and o is None:
                continue
            out[ts] = {
                "time_ms": ts,
                "open": o if o is not None else c,
                "high": h if h is not None else c,
                "low": l if l is not None else c,
                "close": c if c is not None else o,
                "volume_usd": vol if vol is not None else 0.0,
            }
    return out


def write_hourly_csv(path: Path, rows_by_ts: dict[int, dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OHLC_HEADER)
        writer.writeheader()
        for ts in sorted(rows_by_ts.keys()):
            r = rows_by_ts[ts]
            writer.writerow(
                {
                    "time_ms": r["time_ms"],
                    "open": r["open"],
                    "high": r["high"],
                    "low": r["low"],
                    "close": r["close"],
                    "volume_usd": r["volume_usd"],
                }
            )
    return len(rows_by_ts)


def merge_hourly(
    base: dict[int, dict[str, Any]],
    incoming: dict[int, dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], int]:
    added = 0
    merged = dict(base)
    for ts, row in incoming.items():
        if ts not in merged:
            added += 1
        merged[ts] = row
    return merged, added


def rows_from_coinglass_cache(path: Path) -> dict[int, dict[str, Any]]:
    rows = read_hourly_csv(path)
    out: dict[int, dict[str, Any]] = {}
    for ts, r in rows.items():
        out[ts] = {
            "time_ms": ts,
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume_usd": float(r.get("volume_usd") or 0.0),
        }
    return out


def daily_rows_as_hourly(path: Path) -> dict[int, dict[str, Any]]:
    """Use noon UTC on each daily bar when hourly data is missing for that day."""
    raw = read_hourly_csv(path)
    out: dict[int, dict[str, Any]] = {}
    for ts, r in raw.items():
        d = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()
        noon = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)
        noon_ms = int(noon.timestamp() * 1000)
        out[noon_ms] = {
            "time_ms": noon_ms,
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume_usd": float(r.get("volume_usd") or 0.0),
        }
    return out


def fetch_coinglass_batches(
    cg: Any,
    api_key: str,
    *,
    start_ms: int,
    end_ms: int,
    sleep: float,
    retry: int,
) -> dict[int, dict[str, Any]]:
    """Paginate backwards with end_time until start_ms or empty response."""
    out: dict[int, dict[str, Any]] = {}
    cursor_end: int | None = end_ms
    start_ms_cut = start_ms - DAY_MS

    while cursor_end is not None and cursor_end >= start_ms_cut:
        params: dict[str, str] = {
            "exchange": EXCHANGE,
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "limit": str(API_LIMIT),
            "end_time": str(cursor_end),
        }
        status, payload = cg.http_get_json(
            cg.API_BASE + API_PATH,
            params=params,
            api_key=api_key,
            retries=retry,
        )
        if status != 200 or not isinstance(payload, dict):
            raise RuntimeError(f"Coinglass HTTP {status}: {str(payload)[:300]}")
        if str(payload.get("code", "")) != "0":
            msg = str(payload.get("msg") or "")
            if str(payload.get("code")) == "400" and "earliest allowed start_time" in msg:
                floor_m = re.search(r"earliest allowed start_time is (\d+)", msg)
                if floor_m:
                    print(
                        f"[api] plan history floor "
                        f"{cg.ts_to_date(int(floor_m.group(1)))} — stop backfill"
                    )
                break
            raise RuntimeError(
                f"Coinglass code={payload.get('code')!r} msg={payload.get('msg')!r}"
            )
        rows = cg.extract_ohlc_rows(payload.get("data") or [])
        if not rows:
            break

        batch_min = None
        for row in rows:
            ts = int(row["time_ms"])
            if ts < start_ms_cut:
                continue
            o = _to_float(row.get("open")) or _to_float(row.get("close")) or 0.0
            h = _to_float(row.get("high")) or o
            l = _to_float(row.get("low")) or o
            c = _to_float(row.get("close")) or o
            vol = _to_float(row.get("volume_usd")) or 0.0
            out[ts] = {
                "time_ms": ts,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume_usd": vol,
            }
            batch_min = ts if batch_min is None else min(batch_min, ts)

        if batch_min is None:
            break
        if batch_min <= start_ms_cut:
            break
        cursor_end = batch_min - HOUR_MS
        time.sleep(sleep)

    return out


def fetch_coinglass_forward(
    cg: Any,
    api_key: str,
    *,
    since_ms: int,
    sleep: float,
    retry: int,
) -> dict[int, dict[str, Any]]:
    """Incremental forward fetch from since_ms (recheck window)."""
    params: dict[str, str] = {
        "exchange": EXCHANGE,
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "limit": str(API_LIMIT),
        "start_time": str(max(0, since_ms)),
    }
    status, payload = cg.http_get_json(
        cg.API_BASE + API_PATH,
        params=params,
        api_key=api_key,
        retries=retry,
    )
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"Coinglass HTTP {status}: {str(payload)[:300]}")
    if str(payload.get("code", "")) != "0":
        raise RuntimeError(f"Coinglass code={payload.get('code')!r} msg={payload.get('msg')!r}")

    out: dict[int, dict[str, Any]] = {}
    for row in cg.extract_ohlc_rows(payload.get("data") or []):
        ts = int(row["time_ms"])
        o = _to_float(row.get("open")) or _to_float(row.get("close")) or 0.0
        h = _to_float(row.get("high")) or o
        l = _to_float(row.get("low")) or o
        c = _to_float(row.get("close")) or o
        vol = _to_float(row.get("volume_usd")) or 0.0
        out[ts] = {
            "time_ms": ts,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume_usd": vol,
        }
    return out


def fill_daily_gaps(
    hourly: dict[int, dict[str, Any]],
    daily_path: Path,
    start_date: date,
) -> tuple[dict[int, dict[str, Any]], int]:
    """Add synthetic noon bars from daily cache for dates without any hourly row."""
    if not daily_path.is_file():
        return hourly, 0

    covered_days: set[date] = set()
    for ts in hourly:
        covered_days.add(datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date())

    daily_as_hourly = daily_rows_as_hourly(daily_path)
    added = 0
    merged = dict(hourly)
    for ts, row in daily_as_hourly.items():
        d = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()
        if d < start_date or d in covered_days:
            continue
        merged[ts] = row
        covered_days.add(d)
        added += 1
    return merged, added


def build_daily_vwap(hourly: dict[int, dict[str, Any]]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for ts in sorted(hourly.keys()):
        r = hourly[ts]
        d = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        tp = typical_price(r["open"], r["high"], r["low"], r["close"])
        vol = float(r.get("volume_usd") or 0.0)
        records.append({"date": d, "tp": tp, "vol": vol, "close": float(r["close"])})

    if not records:
        return pd.DataFrame(columns=["date", "vwap_usd"])

    df = pd.DataFrame(records)
    grouped = df.groupby("date", sort=True)

    rows: list[dict[str, str | float]] = []
    for day, g in grouped:
        vol_sum = g["vol"].sum()
        if vol_sum > 0:
            vwap = (g["tp"] * g["vol"]).sum() / vol_sum
        else:
            closes = g["close"].replace(0, pd.NA).dropna()
            vwap = closes.mean() if not closes.empty else float("nan")
        if pd.notna(vwap):
            rows.append({"date": day, "vwap_usd": float(vwap)})

    return pd.DataFrame(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download BTC hourly OHLC and build daily VWAP")
    parser.add_argument(
        "--start-date",
        default=DEFAULT_START_DATE.isoformat(),
        help=f"Earliest calendar date (UTC), default {DEFAULT_START_DATE}",
    )
    parser.add_argument("--force", action="store_true", help="Re-download full hourly history from API")
    parser.add_argument("--skip-api", action="store_true", help="Only merge local caches, do not call API")
    parser.add_argument("--recheck-days", type=int, default=3, help="Forward API recheck window when not --force")
    parser.add_argument("--sleep", type=float, default=0.25, help="Pause between Coinglass pages")
    parser.add_argument("--retry", type=int, default=3)
    parser.add_argument("--api-key", default=None, help="Coinglass API key (else env / _common default)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    start_ms = int(datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc).timestamp() * 1000)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    hourly: dict[int, dict[str, Any]] = {}
    if not args.force and HOURLY_CSV.is_file():
        hourly = read_hourly_csv(HOURLY_CSV)
        print(f"[load] {HOURLY_CSV.name}: {len(hourly):,} rows")

    if LOCAL_1H_CACHE.is_file():
        local_1h = rows_from_coinglass_cache(LOCAL_1H_CACHE)
        hourly, n = merge_hourly(hourly, local_1h)
        print(f"[merge] {LOCAL_1H_CACHE.name}: +{n:,} new rows (total {len(hourly):,})")

    if not args.skip_api:
        cg = _load_coinglass_common()
        if cg is None:
            print("[WARN] coinglass-history/_common.py not found; skipping API fetch")
        else:
            api_key = args.api_key or cg.resolve_api_key(None)
            try:
                if args.force or not hourly:
                    print("[api] backfill hourly (paginate with end_time)...")
                    fetched = fetch_coinglass_batches(
                        cg,
                        api_key,
                        start_ms=start_ms,
                        end_ms=now_ms,
                        sleep=args.sleep,
                        retry=args.retry,
                    )
                    hourly, n = merge_hourly(hourly, fetched)
                    print(f"[api] backfill +{n:,} rows (total {len(hourly):,})")
                else:
                    max_ts = max(hourly.keys())
                    since_ms = max(start_ms, max_ts - args.recheck_days * DAY_MS)
                    print(f"[api] forward update since {cg.ts_to_date(since_ms)}...")
                    fetched = fetch_coinglass_forward(
                        cg,
                        api_key,
                        since_ms=since_ms,
                        sleep=args.sleep,
                        retry=args.retry,
                    )
                    hourly, n = merge_hourly(hourly, fetched)
                    print(f"[api] forward +{n:,} rows (total {len(hourly):,})")
            except Exception as exc:
                print(f"[WARN] Coinglass API failed: {exc}")
                if not hourly:
                    raise

    hourly, daily_fill = fill_daily_gaps(hourly, LOCAL_1D_CACHE, start_date)
    if daily_fill:
        print(f"[merge] daily fallback from {LOCAL_1D_CACHE.name}: +{daily_fill:,} days")

    # Drop rows before start_date
    hourly = {
        ts: r
        for ts, r in hourly.items()
        if datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date() >= start_date
    }
    if not hourly:
        raise SystemExit("No hourly rows available. Check API key, cache paths, or --skip-api.")

    n_hourly = write_hourly_csv(HOURLY_CSV, hourly)
    vwap_df = build_daily_vwap(hourly)
    vwap_df.to_csv(DAILY_VWAP_CSV, index=False, encoding="utf-8-sig")

    min_day = vwap_df["date"].min() if not vwap_df.empty else "?"
    max_day = vwap_df["date"].max() if not vwap_df.empty else "?"
    print(f"[done] {HOURLY_CSV.name}: {n_hourly:,} hourly rows")
    print(f"[done] {DAILY_VWAP_CSV.name}: {len(vwap_df):,} days ({min_day} ~ {max_day})")


if __name__ == "__main__":
    main()
