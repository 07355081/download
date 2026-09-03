"""One-time historical mNAV backfill (new official formula).

Not part of the daily job. Future points come from 1.download.py snapshots.

Formula (strategy.com /notes, since 2026-07-23):
  mNAV = MSTR price / Net BPS($)
  Net BPS($) = Net Reserve / FDSO
  Net Reserve = BTC holdings * BTC price
                - OTM convertible notionals
                - preferred notional (except ITM STRK)
                - other senior debt
                + USD cash
  FDSO = ADSO - OTM convert share equivalents - OTM STRK share equivalents
       = basic + options + RSU + ITM converts + ITM STRK

Public inputs (downloaded once into cache/backfill/):
  - strategy.com/ledger  BTC holdings + weekly ADSO
  - strategy.com/shares  convert/STRK/basic/options snapshots (split-adjusted)
  - Yahoo MSTR + BTC-USD daily closes (fallback: Stooq)

Usage:
    python 2.backfill_mnav.py
    python 2.backfill_mnav.py --refresh-prices
"""
from __future__ import annotations

import argparse
import gzip
import html as html_lib
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache" / "backfill"
OUTPUT_JSON = HERE / "output" / "json"
ORIGIN = "https://www.strategy.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
CTX = ssl.create_default_context()
MNAV_DEFINITION_CHANGE = date(2026, 7, 23)
START = date(2020, 8, 11)

# Shares table on /shares, split-adjusted, figures in thousands.
SHARES_DATES = [
    date(2020, 12, 31),
    date(2021, 12, 31),
    date(2022, 12, 31),
    date(2023, 12, 31),
    date(2024, 12, 31),
    date(2025, 12, 31),
    date(2026, 3, 31),
    date(2026, 6, 30),
    date(2026, 8, 9),
]
BASIC_K = [95870, 112855, 115488, 168681, 245778, 312062, 346223, 371604, 394203]
OPTIONS_K = [11570, 11668, 15769, 12936, 4956, 3662, 3315, 3176, 3154]
RSU_K = [740, 1051, 1196, 2359, 1845, 1335, 1454, 892, 882]
ADSO_K = [124510, 149234, 156113, 207636, 281735, 344897, 378834, 401283, 423850]

# Convert share equivalents in thousands and conversion price (USD, post 10-for-1).
CONVERTS: list[tuple[str, float, list[int]]] = [
    ("2025", 39.80, [16330, 16330, 16330, 16330, 0, 0, 0, 0, 0]),
    ("2027", 143.25, [0, 7330, 7330, 7330, 7330, 0, 0, 0, 0]),
    ("2028", 183.19, [0, 0, 0, 0, 5513, 5513, 5513, 5513, 5513]),
    ("2029", 672.40, [0, 0, 0, 0, 4462, 4462, 4462, 2231, 2231]),
    ("2030A", 149.77, [0, 0, 0, 0, 5342, 5342, 5342, 5342, 5342]),
    ("2030B", 433.43, [0, 0, 0, 0, 0, 4614, 4614, 4614, 4614]),
    ("2031", 232.72, [0, 0, 0, 0, 2594, 2594, 2594, 2594, 2594]),
    ("2032", 204.33, [0, 0, 0, 0, 3915, 3915, 3915, 3915, 3915]),
]
STRK_K = [0, 0, 0, 0, 0, 1398, 1402, 1402, 1402]
STRK_CONV = 1000.0

# 6.125% senior secured notes, redeemed with 2028 convert proceeds.
SENIOR_SECURED = (date(2021, 6, 11), date(2024, 9, 19), 500_000_000.0)

# Preferred liquidation preference (includes STRK). Linear between waypoints.
PREF_TOTAL = [
    (date(2025, 1, 29), 0.0),
    (date(2025, 12, 31), 7_122_000_000.0),  # STRF+STRC+STRK+STRD YE2025 10-K
    (date(2026, 2, 13), 8_470_000_000.0),
    (date(2026, 5, 26), 15_500_000_000.0),
    (date(2026, 7, 23), 15_500_000_000.0),
    (date(2026, 8, 16), 15_239_000_000.0),
]

# USD reserve. Negligible vs BTC before the 2026 cash program.
CASH_POINTS = [
    (date(2020, 8, 11), 0.0),
    (date(2025, 12, 31), 0.0),
    (date(2026, 5, 26), 871_000_000.0),
    (date(2026, 7, 23), 3_200_000_000.0),
    (date(2026, 8, 16), 4_650_000_000.0),
]


def log(msg: str) -> None:
    print(msg, flush=True)


def http_get(url: str, timeout: int = 90, retries: int = 3) -> bytes:
    last_err: Exception | None = None
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/json,text/csv,*/*",
            "Referer": f"{ORIGIN}/",
            "Origin": ORIGIN,
        },
    )
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
                body = resp.read()
                if body[:2] == b"\x1f\x8b":
                    body = gzip.decompress(body)
                return body
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_err = exc
            log(f"  retry {attempt}/{retries} {url} ({exc})")
            time.sleep(min(2 * attempt, 6))
    raise RuntimeError(f"GET failed {url}: {last_err}")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_num(text: str) -> float | None:
    raw = html_lib.unescape(text)
    raw = re.sub(r"[₿$,()]", "", raw).replace(",", "").strip()
    if not raw or raw in ("-", "—"):
        return None
    neg = raw.startswith("-") or "(" in text
    raw = raw.replace("-", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    return -value if neg else value


def parse_mdy(text: str) -> date | None:
    text = text.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def lerp_points(points: list[tuple[date, float]], day: date) -> float:
    if not points:
        return 0.0
    ordered = sorted(points, key=lambda p: p[0])
    if day <= ordered[0][0]:
        return ordered[0][1]
    if day >= ordered[-1][0]:
        return ordered[-1][1]
    for i in range(1, len(ordered)):
        d0, v0 = ordered[i - 1]
        d1, v1 = ordered[i]
        if day <= d1:
            span = (d1 - d0).days
            if span <= 0:
                return v1
            w = (day - d0).days / span
            return v0 + (v1 - v0) * w
    return ordered[-1][1]


def step_lookup(dates: list[date], values: list[float], day: date) -> float:
    """Last snapshot on or before `day`; if before first snapshot, first value."""
    last = values[0]
    for d, v in zip(dates, values):
        if d <= day:
            last = v
        else:
            break
    return float(last)


def lerp_lookup(dates: list[date], values: list[float], day: date) -> float:
    return lerp_points(list(zip(dates, [float(v) for v in values])), day)


def daterange(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def parse_ledger_html(html: str) -> list[dict[str, Any]]:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(\{.*\})</script>', html)
    if not m:
        raise RuntimeError("ledger page missing __NEXT_DATA__")
    blob = json.loads(m.group(1))
    raw_rows = (
        ((blob.get("props") or {}).get("pageProps") or {}).get("bitcoinData")
        or []
    )
    rows: list[dict[str, Any]] = []
    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        day_raw = str(item.get("date_of_purchase") or "")[:10]
        try:
            day = date.fromisoformat(day_raw)
        except ValueError:
            continue
        btc = parse_num(str(item.get("btc_holdings")))
        if not btc:
            continue
        adso = parse_num(str(item.get("assumed_diluted_shares_outstanding"))) if item.get("assumed_diluted_shares_outstanding") not in (None, "", "-") else None
        basic = parse_num(str(item.get("basic_shares_outstanding"))) if item.get("basic_shares_outstanding") not in (None, "", "-") else None
        rows.append({"date": day, "btc": btc, "adso": adso, "basic": basic})
    rows.sort(key=lambda r: r["date"])
    return rows


def download_ledger() -> list[dict[str, Any]]:
    path = CACHE / "ledger.html"
    log("GET strategy.com/ledger")
    html = http_get(f"{ORIGIN}/ledger", timeout=60).decode("utf-8", "replace")
    write_text(path, html)
    rows = parse_ledger_html(html)
    log(f"  ledger rows: {len(rows)}")
    if len(rows) < 50:
        raise RuntimeError("failed to parse ledger (expected ~118 purchase rows)")
    rows.sort(key=lambda r: r["date"])
    write_json(
        CACHE / "ledger.json",
        [
            {
                "date": r["date"].isoformat(),
                "btc": r["btc"],
                "adso": r.get("adso"),
                "basic": r.get("basic"),
            }
            for r in rows
        ],
    )
    return rows


def extract_close_series(payload: Any, field: str = "price") -> dict[str, float]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        maybe = payload.get("data") or payload.get("results") or []
        rows = maybe if isinstance(maybe, list) else [payload]
    else:
        rows = []
    out: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        values = row.get("values") if isinstance(row.get("values"), list) else [row]
        for item in values:
            if not isinstance(item, dict):
                continue
            raw_day = str(item.get("date") or item.get("t") or "")[:10]
            if len(raw_day) < 10:
                continue
            px = item.get(field)
            if px is None:
                px = item.get("close") or item.get("latestPrice")
            try:
                value = float(str(px).replace(",", ""))
            except (TypeError, ValueError):
                continue
            if value > 0:
                out[raw_day] = value
    return out


def download_strategy_time_series(ticker: str, metric: str, start: date, end: date) -> dict[str, float]:
    merged: dict[str, float] = {}
    year = start.year
    while date(year, 1, 1) <= end:
        chunk_start = max(start, date(year, 1, 1))
        chunk_end = min(end, date(year, 12, 31))
        qs = urllib.parse.urlencode(
            {
                "from": chunk_start.isoformat(),
                "to": chunk_end.isoformat(),
                "tickers": ticker,
                "metrics": metric,
            }
        )
        url = f"https://api.strategy.com/btc/timeSeries?{qs}"
        log(f"GET timeSeries {ticker} {metric} {chunk_start} → {chunk_end}")
        raw = http_get(url, timeout=90)
        payload = json.loads(raw.decode("utf-8"))
        part = extract_close_series(payload, metric)
        log(f"  points: {len(part)}")
        merged.update(part)
        year += 1
        time.sleep(0.2)
    return merged


def load_local_btc_spot() -> dict[str, float]:
    candidates = [
        HERE.parent / "btc-index" / "output" / "json" / "coinglass" / "btc_spot_daily.json",
        Path(r"C:\code\data-dashboard\public\json\btc-index\coinglass\btc_spot_daily.json"),
    ]
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        return {}
    blob = json.loads(path.read_text(encoding="utf-8"))
    rows: Any = blob.get("data") if isinstance(blob, dict) else blob
    if isinstance(rows, dict):
        rows = rows.get("data") or rows.get("series") or rows.get("prices") or []
    out: dict[str, float] = {}
    if isinstance(rows, list):
        for item in rows:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                ts, px = item[0], item[1]
                try:
                    if float(ts) > 10_000_000:
                        day = datetime.fromtimestamp(float(ts) / (1000 if float(ts) > 1e12 else 1), tz=timezone.utc).date().isoformat()
                    else:
                        continue
                    out[day] = float(px)
                except (TypeError, ValueError, OSError):
                    continue
                continue
            if not isinstance(item, dict):
                continue
            day = str(item.get("date") or item.get("t") or item.get("time") or "")[:10]
            px = item.get("price") or item.get("close") or item.get("value")
            try:
                value = float(px)
            except (TypeError, ValueError):
                continue
            if day and value > 0:
                out[day] = value
    log(f"local btc_spot_daily: {len(out)} from {path}")
    return out


def download_coingecko_btc() -> dict[str, float]:
    url = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=max&interval=daily"
    log("GET CoinGecko bitcoin market_chart")
    raw = http_get(url, timeout=60)
    data = json.loads(raw.decode("utf-8"))
    out: dict[str, float] = {}
    for ts, px in data.get("prices") or []:
        day = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).date().isoformat()
        out[day] = float(px)
    log(f"  btc days: {len(out)}")
    return out


def download_binance_btc(start: date, end: date) -> dict[str, float]:
    out: dict[str, float] = {}
    start_ms = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime(end.year, end.month, end.day, 23, 59, tzinfo=timezone.utc).timestamp() * 1000)
    cursor = start_ms
    log("GET Binance BTCUSDT daily klines")
    while cursor < end_ms:
        qs = urllib.parse.urlencode(
            {
                "symbol": "BTCUSDT",
                "interval": "1d",
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            }
        )
        raw = http_get(f"https://api.binance.com/api/v3/klines?{qs}", timeout=30)
        rows = json.loads(raw.decode("utf-8"))
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            ts = int(row[0])
            close = float(row[4])
            day = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date().isoformat()
            out[day] = close
        last_open = int(rows[-1][0])
        nxt = last_open + 86_400_000
        if nxt <= cursor:
            break
        cursor = nxt
        if len(rows) < 1000:
            break
        time.sleep(0.15)
    log(f"  binance btc days: {len(out)}")
    return out


def download_yahoo(symbol: str, start: date, end: date) -> dict[str, float]:
    period1 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    period2 = int(datetime(end.year, end.month, end.day, 23, 59, tzinfo=timezone.utc).timestamp())
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(symbol)
        + "?"
        + urllib.parse.urlencode(
            {
                "period1": period1,
                "period2": period2,
                "interval": "1d",
                "includeAdjustedClose": "true",
                "events": "div,split",
            }
        )
    )
    log(f"GET Yahoo {symbol}")
    raw = http_get(url, timeout=60)
    data = json.loads(raw.decode("utf-8"))
    result = (((data.get("chart") or {}).get("result") or [None])[0]) or {}
    ts = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    adj = ((result.get("indicators") or {}).get("adjclose") or [{}])[0]
    closes = adj.get("adjclose") or quote.get("close") or []
    out: dict[str, float] = {}
    for t, c in zip(ts, closes):
        if c is None:
            continue
        day = datetime.fromtimestamp(int(t), tz=timezone.utc).date().isoformat()
        out[day] = float(c)
    log(f"  {symbol} days: {len(out)}")
    return out


def download_stooq(symbol: str) -> dict[str, float]:
    url = f"https://stooq.com/q/d/l/?s={urllib.parse.quote(symbol)}&i=d"
    log(f"GET Stooq {symbol}")
    raw = http_get(url, timeout=60).decode("utf-8", "replace")
    out: dict[str, float] = {}
    for line in raw.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 5:
            continue
        day, close = parts[0].strip(), parts[4].strip()
        try:
            out[day] = float(close)
        except ValueError:
            continue
    log(f"  {symbol} days: {len(out)}")
    return out


def load_prices(refresh: bool) -> tuple[dict[str, float], dict[str, float]]:
    mstr_path = CACHE / "mstr_close.json"
    btc_path = CACHE / "btc_close.json"
    today = date.today()
    if mstr_path.exists() and btc_path.exists() and not refresh:
        mstr = {k: float(v) for k, v in json.loads(mstr_path.read_text(encoding="utf-8")).items()}
        btc = {k: float(v) for k, v in json.loads(btc_path.read_text(encoding="utf-8")).items()}
        if len(mstr) >= 200 and len(btc) >= 200:
            log(f"reuse cached prices mstr={len(mstr)} btc={len(btc)}")
            return mstr, btc

    mstr: dict[str, float] = {}
    try:
        mstr = download_strategy_time_series("MSTR", "price", START, today)
    except Exception as exc:
        log(f"  strategy MSTR price failed: {exc}")
    if len(mstr) < 200:
        try:
            mstr = download_yahoo("MSTR", START, today)
        except Exception as exc:
            log(f"  yahoo MSTR failed: {exc}")
            mstr = download_stooq("mstr.us") or download_stooq("mstr.us.txt")

    btc = load_local_btc_spot()
    try:
        btc_api = download_strategy_time_series("BTC", "price", date(2020, 8, 1), today)
        if btc_api:
            btc.update(btc_api)
            log(f"  btc after strategy timeSeries: {len(btc)}")
    except Exception as exc:
        log(f"  strategy BTC price failed: {exc}")
    last_btc = max(btc) if btc else ""
    if last_btc < (today - timedelta(days=3)).isoformat():
        try:
            btc.update(download_binance_btc(START, today))
            log(f"  btc after Binance: {len(btc)} last={max(btc) if btc else None}")
        except Exception as exc:
            log(f"  binance BTC failed: {exc}")

    if len(mstr) < 200 or len(btc) < 200:
        raise RuntimeError(f"price series too short mstr={len(mstr)} btc={len(btc)}")
    write_json(mstr_path, mstr)
    write_json(btc_path, btc)
    return mstr, btc


def last_price(series: dict[str, float], day: date) -> float | None:
    key = day.isoformat()
    if key in series:
        return series[key]
    ordered = getattr(last_price, "_cache", None)
    cache_id = getattr(last_price, "_id", None)
    if cache_id is not id(series):
        ordered = sorted((date.fromisoformat(k), v) for k, v in series.items() if v and v > 0)
        last_price._cache = ordered  # type: ignore[attr-defined]
        last_price._id = id(series)  # type: ignore[attr-defined]
    if not ordered:
        return None
    lo, hi = 0, len(ordered) - 1
    if day < ordered[0][0]:
        return None
    ans = ordered[0][1]
    while lo <= hi:
        mid = (lo + hi) // 2
        if ordered[mid][0] <= day:
            ans = ordered[mid][1]
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


def convert_state(day: date, mstr_px: float) -> tuple[float, float, float]:
    """Return (otm_notional, otm_share_equivalents, itm_share_equivalents)."""
    otm_notional = 0.0
    otm_shares = 0.0
    itm_shares = 0.0
    for _name, conv_px, series in CONVERTS:
        shares_k = step_lookup(SHARES_DATES, [float(x) for x in series], day)
        shares = shares_k * 1000.0
        if shares <= 0:
            continue
        notional = shares * conv_px
        if mstr_px < conv_px:
            otm_notional += notional
            otm_shares += shares
        else:
            itm_shares += shares
    strk_k = step_lookup(SHARES_DATES, [float(x) for x in STRK_K], day)
    strk_shares = strk_k * 1000.0
    if strk_shares > 0:
        if mstr_px < STRK_CONV:
            otm_notional += strk_shares * STRK_CONV
            otm_shares += strk_shares
        else:
            itm_shares += strk_shares
    start, end, amt = SENIOR_SECURED
    if start <= day <= end:
        otm_notional += amt
    return otm_notional, otm_shares, itm_shares


def holdings_on(ledger: list[dict[str, Any]], day: date) -> tuple[float, float | None, float | None]:
    btc = 0.0
    adso = None
    basic = None
    for row in ledger:
        if row["date"] <= day:
            btc = float(row["btc"])
            if row.get("adso"):
                adso = float(row["adso"])
            if row.get("basic"):
                basic = float(row["basic"])
        else:
            break
    return btc, adso, basic


def compute_series(
    ledger: list[dict[str, Any]],
    mstr_px: dict[str, float],
    btc_px: dict[str, float],
    end: date,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    trading_days = sorted(d for d in mstr_px if START.isoformat() <= d <= end.isoformat())
    for key in trading_days:
        day = date.fromisoformat(key)
        px = mstr_px[key]
        btc_price = last_price(btc_px, day)
        if px is None or btc_price is None or px <= 0 or btc_price <= 0:
            continue
        btc_hold, ledger_adso, ledger_basic = holdings_on(ledger, day)
        if btc_hold <= 0:
            continue
        otm_notional, otm_shares, itm_shares = convert_state(day, px)
        pref = lerp_points(PREF_TOTAL, day)
        strk_k = step_lookup(SHARES_DATES, [float(x) for x in STRK_K], day)
        strk_notional = strk_k * 1000.0 * STRK_CONV
        other_pref = max(pref - strk_notional, 0.0)
        cash = lerp_points(CASH_POINTS, day)
        gross = btc_hold * btc_price
        net_reserve = gross - otm_notional - other_pref + cash
        options = lerp_lookup(SHARES_DATES, [float(x) for x in OPTIONS_K], day) * 1000.0
        rsu = lerp_lookup(SHARES_DATES, [float(x) for x in RSU_K], day) * 1000.0
        if ledger_basic and ledger_basic > 1_000_000:
            fdso = ledger_basic + options + rsu + itm_shares
        elif ledger_adso and ledger_adso > 1_000_000:
            fdso = ledger_adso - otm_shares
        else:
            adso = lerp_lookup(SHARES_DATES, [float(x) for x in ADSO_K], day) * 1000.0
            fdso = adso - otm_shares
        if fdso <= 0:
            basic = lerp_lookup(SHARES_DATES, [float(x) for x in BASIC_K], day) * 1000.0
            fdso = basic + options + rsu + itm_shares
        if fdso <= 0 or net_reserve <= 0:
            continue
        net_bps = net_reserve / fdso
        mnav = px / net_bps
        if not (0.05 < mnav < 50):
            continue
        out.append(
            {
                "t": key,
                "mnav": round(mnav, 4),
                "source": "reconstructed",
                "mstr": round(px, 4),
                "btc": round(btc_price, 2),
                "holdings": btc_hold,
                "net_reserve": round(net_reserve, 2),
                "fdso": round(fdso),
                "net_bps": round(net_bps, 4),
            }
        )
    return out


def merge_into_history(reconstructed: list[dict[str, Any]]) -> None:
    path = OUTPUT_JSON / "mnav_history.json"
    existing_series: list[dict[str, Any]] = []
    meta: dict[str, Any] = {}
    if path.exists():
        prev = json.loads(path.read_text(encoding="utf-8"))
        data = prev.get("data") if isinstance(prev, dict) else None
        if isinstance(data, dict):
            meta = data
            existing_series = list(data.get("series") or [])
    by_t: dict[str, dict[str, Any]] = {}
    for point in reconstructed:
        slim = {"t": point["t"], "mnav": point["mnav"], "source": "reconstructed"}
        by_t[point["t"]] = slim
    for point in existing_series:
        src = str(point.get("source") or "")
        day = str(point.get("t") or "")
        if not day:
            continue
        if src.startswith("official"):
            by_t[day] = {"t": day, "mnav": point["mnav"], "source": src}
        elif src == "reconstructed":
            continue
        elif src == "backcast":
            # replaced by formula reconstruction
            continue
        else:
            by_t[day] = {"t": day, "mnav": point["mnav"], "source": src}
    series = [by_t[k] for k in sorted(by_t)]
    payload = {
        "code": 0,
        "msg": "success",
        "data": {
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": ORIGIN,
            "mnav_definition": "MSTR price / Net Bitcoin Per Share ($)",
            "mnav_definition_change_date": MNAV_DEFINITION_CHANGE.isoformat(),
            "reconstruction": (
                "New-definition backcast from strategy.com ledger + /shares and public BTC/MSTR prices. "
                "Not contemporaneous website mNAV before 2026-07-23."
            ),
            "series": series,
        },
    }
    # preserve extra meta keys if present
    for key in ("api",):
        if key in meta:
            payload["data"][key] = meta[key]
    write_json(path, payload)
    recon_n = sum(1 for p in series if p["source"] == "reconstructed")
    off_n = sum(1 for p in series if str(p["source"]).startswith("official"))
    log(f"wrote {path} points={len(series)} reconstructed={recon_n} official={off_n}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-prices", action="store_true")
    parser.add_argument("--refresh-ledger", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.mkdir(parents=True, exist_ok=True)

    ledger_json = CACHE / "ledger.json"
    if ledger_json.exists() and not args.refresh_ledger:
        ledger = []
        for row in json.loads(ledger_json.read_text(encoding="utf-8")):
            ledger.append(
                {
                    "date": date.fromisoformat(row["date"]),
                    "btc": float(row["btc"]),
                    "adso": float(row["adso"]) if row.get("adso") else None,
                    "basic": float(row["basic"]) if row.get("basic") else None,
                }
            )
        log(f"reuse cached ledger rows={len(ledger)}")
    else:
        ledger = download_ledger()

    mstr_px, btc_px = load_prices(args.refresh_prices)
    latest_path = OUTPUT_JSON / "latest.json"
    if latest_path.exists():
        try:
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
            data = latest.get("data") if isinstance(latest, dict) else {}
            cash = ((data.get("btc") or {}) if isinstance(data, dict) else {}).get("usd_reserve_cash")
            pref = ((data.get("mstr") or {}) if isinstance(data, dict) else {}).get("pref_usd")
            fetched = str((data or {}).get("fetched_at") or "")[:10]
            pin_day = date.fromisoformat(fetched) if len(fetched) >= 10 else date.today()
            if cash:
                CASH_POINTS.append((pin_day, float(cash)))
            if pref:
                PREF_TOTAL.append((pin_day, float(pref)))
        except Exception as exc:
            log(f"  latest.json pin skipped: {exc}")
    reconstructed = compute_series(ledger, mstr_px, btc_px, date.today())
    write_json(
        CACHE / "mnav_reconstructed_detail.json",
        reconstructed,
    )
    log(f"reconstructed points: {len(reconstructed)}")
    if reconstructed:
        log(f"  first {reconstructed[0]['t']} mNAV={reconstructed[0]['mnav']}")
        log(f"  last  {reconstructed[-1]['t']} mNAV={reconstructed[-1]['mnav']}")
        for probe in ("2024-11-21", "2026-07-22", "2026-07-23", "2026-08-14"):
            hit = next((p for p in reconstructed if p["t"] == probe), None)
            if hit:
                log(
                    f"  {probe} mNAV={hit['mnav']} netBPS={hit['net_bps']} "
                    f"MSTR={hit['mstr']} BTC={hit['btc']} fdso={hit['fdso']:,}"
                )
    merge_into_history(reconstructed)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log("interrupted")
        raise SystemExit(130)
    except Exception as exc:
        log(f"ERROR: {exc}")
        raise SystemExit(1) from exc
