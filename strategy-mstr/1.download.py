"""Download Strategy.com MSTR homepage KPIs (daily job).

Default (incremental, no history re-download):
  - output/json/latest.json
  - output/json/snapshots/YYYY-MM-DD.json  (overwrite same local day)
  - overlay today's mNAV / wipeout onto existing history files

Reconstructed history is a one-time product of `2.backfill_mnav.py`.
Use --full-history only if you need to refresh the official timeSeries overlay.

Usage:
    python 1.download.py
    python 1.download.py --full-history
"""
from __future__ import annotations

import argparse
import gzip
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUTPUT_JSON = HERE / "output" / "json"
SNAPSHOT_DIR = OUTPUT_JSON / "snapshots"
CACHE_DIR = HERE / "cache"

API = "https://api.strategy.com"
ORIGIN = "https://www.strategy.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
CTX = ssl.create_default_context()

MNAV_DEFINITION_CHANGE = date(2026, 7, 23)
# First Strategy BTC purchase was 2020-08; request extra history so backcast
# points are kept if the API still serves pre-change mNav.
HISTORY_FROM = date(2020, 8, 11)
DETAIL_PATH = CACHE_DIR / "backfill" / "mnav_reconstructed_detail.json"


def log(msg: str) -> None:
    print(msg, flush=True)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def local_today() -> date:
    return datetime.now().astimezone().date()


def envelope(data: Any) -> dict[str, Any]:
    return {"code": 0, "msg": "success", "data": data}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_num(value: Any) -> float | None:
    if value is None or value is False:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:  # NaN
            return None
        return float(value)
    text = str(value).strip().replace(",", "").replace("%", "").replace("$", "")
    if text in ("", "--", "-", "n/a", "N/A", "null"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def millions_to_usd(value: Any) -> float | None:
    num = parse_num(value)
    if num is None:
        return None
    return num * 1_000_000.0


def _round_or_none(value: float | None, digits: int) -> float | None:
    if value is None or value != value:
        return None
    return round(float(value), digits)


def derive_solvency(
    *,
    btc_px: float | None,
    holdings: float | None,
    net_reserve: float | None,
    reserve: float | None = None,
    amplification: float | None = None,
    t: str | None = None,
    source: str | None = None,
) -> dict[str, Any] | None:
    """Wipeout price = senior claims / BTC holdings; claims = Reserve − Net Reserve."""
    if holdings is None or holdings <= 0 or btc_px is None or btc_px <= 0 or net_reserve is None:
        return None
    gross = reserve if reserve is not None else holdings * btc_px
    claims = gross - net_reserve
    wipeout = claims / holdings
    amp = amplification
    if amp is None and net_reserve > 0:
        amp = gross / net_reserve
    cushion_x = (btc_px / wipeout) if wipeout > 0 else None
    cushion_drop_pct = ((btc_px - wipeout) / btc_px) * 100.0 if btc_px else None
    net_ratio = (net_reserve / gross) if gross else None
    out: dict[str, Any] = {
        "claims_usd": _round_or_none(claims, 2),
        "wipeout_px": _round_or_none(wipeout, 2),
        "cushion_x": _round_or_none(cushion_x, 4),
        "cushion_drop_pct": _round_or_none(cushion_drop_pct, 2),
        "net_ratio": _round_or_none(net_ratio, 4),
        "amplification": _round_or_none(amp, 4),
    }
    if t:
        out["t"] = t
        out["btc"] = _round_or_none(btc_px, 2)
        out["holdings"] = holdings
        out["net_reserve"] = _round_or_none(net_reserve, 2)
        if source:
            out["source"] = source
    return out


def _load_solvency_series(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        prev = json.loads(path.read_text(encoding="utf-8"))
        data = prev.get("data") if isinstance(prev, dict) else None
        if isinstance(data, dict) and isinstance(data.get("series"), list):
            return list(data["series"])
        if isinstance(prev, list):
            return list(prev)
    except Exception:
        return []
    return []


def _bootstrap_solvency_from_detail() -> list[dict[str, Any]]:
    if not DETAIL_PATH.exists():
        return []
    try:
        rows = json.loads(DETAIL_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    points: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        point = derive_solvency(
            btc_px=parse_num(row.get("btc")),
            holdings=parse_num(row.get("holdings")),
            net_reserve=parse_num(row.get("net_reserve")),
            t=iso_day(row.get("t")),
            source=str(row.get("source") or "reconstructed"),
        )
        if point and point.get("t") and point.get("wipeout_px") is not None:
            points.append(point)
    points.sort(key=lambda p: str(p.get("t")))
    return points


def write_solvency_history(latest: dict[str, Any], today: date, fetched_at: str) -> int:
    path = OUTPUT_JSON / "net_reserve_history.json"
    series = _load_solvency_series(path)
    if not series:
        series = _bootstrap_solvency_from_detail()
        log(f"bootstrapped net_reserve_history from reconstructed detail: {len(series)} points")
    live = derive_solvency(
        btc_px=parse_num((latest.get("btc") or {}).get("price")),
        holdings=parse_num((latest.get("btc") or {}).get("holdings")),
        net_reserve=parse_num((latest.get("btc") or {}).get("net_reserve_usd")),
        reserve=parse_num((latest.get("btc") or {}).get("reserve_usd")),
        amplification=parse_num((latest.get("btc") or {}).get("amplification")),
        t=today.isoformat(),
        source="official-live" if today >= MNAV_DEFINITION_CHANGE else "reconstructed",
    )
    by_t: dict[str, dict[str, Any]] = {}
    for point in series:
        day = str(point.get("t") or "")
        if day:
            by_t[day] = point
    if live and live.get("t"):
        by_t[str(live["t"])] = live
    series = [by_t[k] for k in sorted(by_t)]
    payload = {
        "fetched_at": fetched_at,
        "source": ORIGIN,
        "definition": "wipeout_px = (Reserve − Net Reserve) / BTC holdings",
        "mnav_definition_change_date": MNAV_DEFINITION_CHANGE.isoformat(),
        "series": series,
    }
    write_json(path, envelope(payload))
    return len(series)


def iso_day(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "T" in text:
        text = text.split("T", 1)[0]
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None


def decode_body(body: bytes) -> bytes:
    if body[:2] == b"\x1f\x8b":
        return gzip.decompress(body)
    return body


def http_get(url: str, timeout: int = 90, retries: int = 3) -> bytes:
    last_err: Exception | None = None
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json,*/*",
            "Referer": f"{ORIGIN}/",
            "Origin": ORIGIN,
        },
    )
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
                return decode_body(resp.read())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_err = exc
            log(f"  retry {attempt}/{retries} {url} ({exc})")
            time.sleep(min(2 * attempt, 6))
    raise RuntimeError(f"GET failed {url}: {last_err}")


def http_get_json(url: str, timeout: int = 90) -> Any:
    raw = http_get(url, timeout=timeout)
    return json.loads(raw.decode("utf-8"))


def first_row(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                return item
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def bitcoin_results(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        results = payload.get("results")
        if isinstance(results, dict):
            return results
        return payload
    return {}


def pick_extended(obj: dict[str, Any]) -> dict[str, Any]:
    ext = obj.get("extendedSession")
    return ext if isinstance(ext, dict) else {}


def build_latest(
    btc_payload: Any,
    mstr_payload: Any,
    options_payload: Any,
    credit_payload: Any,
    fetched_at: str,
) -> dict[str, Any]:
    btc = bitcoin_results(btc_payload)
    mstr = first_row(mstr_payload)
    options = first_row(options_payload)
    credit = credit_payload if isinstance(credit_payload, dict) else {}
    btc_ext = pick_extended(btc)
    mstr_ext = pick_extended(mstr)

    holdings = parse_num(btc.get("btcHoldings"))
    reserve_usd = millions_to_usd(btc.get("btcNavNumber") if btc.get("btcNavNumber") is not None else btc.get("btcNav"))
    market_cap_usd = millions_to_usd(mstr.get("marketCap"))
    ev_usd = millions_to_usd(mstr.get("entVal"))
    net_reserve = parse_num(btc.get("netBtcReserve"))
    amplification = parse_num(btc.get("amplification"))
    ev_over_gross = None
    if ev_usd and reserve_usd:
        ev_over_gross = ev_usd / reserve_usd

    btc_price = parse_num(btc.get("latestPrice") if btc.get("latestPrice") is not None else btc.get("ufPrice"))
    solvency = derive_solvency(
        btc_px=btc_price,
        holdings=holdings,
        net_reserve=net_reserve,
        reserve=reserve_usd,
        amplification=amplification,
    )

    return {
        "fetched_at": fetched_at,
        "source": ORIGIN,
        "api": API,
        "mnav_definition": "MSTR price / Net Bitcoin Per Share ($)",
        "mnav_definition_change_date": MNAV_DEFINITION_CHANGE.isoformat(),
        "btc": {
            "price": btc_price,
            "prev_day_price": parse_num(btc.get("prevDayPrice")),
            "price_var_pct": parse_num(btc.get("priceVarPerc")),
            "holdings": holdings,
            "pct_of_supply": parse_num(btc.get("pctOfBtcTotalSupply")),
            "reserve_usd": reserve_usd,
            "net_reserve_usd": net_reserve,
            "usd_reserve_cash": parse_num(credit.get("cash")),
            "duration_years": parse_num(btc.get("btcYearsOfDividends")),
            "usd_months_of_dividends": parse_num(btc.get("usdMonthsOfDividends")),
            "mnav": parse_num(btc.get("mNav")),
            "mnav_var_pct": parse_num(btc.get("mNavVarPerc")),
            "mnav_ah": parse_num(btc_ext.get("mNav")),
            "session_type": btc_ext.get("sessionType"),
            "session_time": btc_ext.get("timeStamp"),
            "gross_bps_usd": parse_num(btc.get("btcPerShareUsd")),
            "net_bps_usd": parse_num(btc.get("netBtcPerShareUsd")),
            "sats_per_share": parse_num(btc.get("satsPerShare")),
            "net_sats_per_share": parse_num(btc.get("netSatsPerShare")),
            "amplification": amplification,
            "debt_bn": parse_num(btc.get("debtByBN")),
            "pref_bn": parse_num(btc.get("prefByBN")),
            "hurdle_arr_pct": parse_num(btc.get("bitcoinHurdleArr")),
            "breakeven_arr_pct": parse_num(btc.get("btcBreakevenArr")),
            "failure_arr_pct": parse_num(btc.get("btcFailureArr")),
            "btc_gain_qtd": parse_num(btc.get("btcGainQtd")),
            "btc_gain_ytd": parse_num(btc.get("btcGainYTD")),
        },
        "solvency": solvency or {},
        "mstr": {
            "price": parse_num(mstr.get("ufPrice") if mstr.get("ufPrice") is not None else mstr.get("price")),
            "price_var_pct": parse_num(mstr.get("priceVarPerc")),
            "price_ah": parse_num(mstr_ext.get("ufPrice") if mstr_ext.get("ufPrice") is not None else mstr_ext.get("price")),
            "session_type": mstr_ext.get("sessionType"),
            "session_time": mstr_ext.get("timeStamp") or mstr.get("timeStamp"),
            "session_time_utc": mstr.get("timeStampUtc"),
            "one_year_return_pct": parse_num(mstr.get("oneYear")),
            "three_month_return_pct": parse_num(mstr.get("threeMonth")),
            "bse_return_pct": parse_num(mstr.get("bse")),
            "bse_annualized_pct": parse_num(mstr.get("bseAnnualized")),
            "market_cap_usd": market_cap_usd,
            "enterprise_value_usd": ev_usd,
            "ev_over_gross_btc_nav": ev_over_gross,
            "volume_usd": millions_to_usd(mstr.get("dailyVolume")),
            "avg_volume_30d_usd": millions_to_usd(mstr.get("averageVolume")),
            "hist_vol_30d_pct": parse_num(mstr.get("historicVolatility")),
            "hist_vol_1y_pct": parse_num(mstr.get("annualizedVolatility")),
            "btc_correlation_pct": parse_num(mstr.get("btcCor")),
            "debt_usd": millions_to_usd(mstr.get("debt")),
            "pref_usd": millions_to_usd(mstr.get("pref")),
            "debt_pref_by_mcap_pct": parse_num(mstr.get("debtPrefByMC")),
        },
        "options": {
            "iv_pct": parse_num(options.get("impliedVolatility")),
            "oi_usd": millions_to_usd(options.get("totalOi")),
            "put_oi_usd": millions_to_usd(options.get("putOi")),
            "call_oi_usd": millions_to_usd(options.get("callOi")),
            "put_call_ratio": parse_num(options.get("putCallRatio")),
            "hist_vol_pct": parse_num(options.get("historicVolatility")),
            "duration_days": parse_num(options.get("duration")),
        },
    }


def extract_mnav_series(payload: Any) -> list[dict[str, Any]]:
    rows: list[Any]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for key in ("data", "results", "values"):
            maybe = payload.get(key)
            if isinstance(maybe, list):
                rows = maybe
                break
        else:
            rows = [payload]
    else:
        rows = []

    points: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        values = row.get("values") if isinstance(row.get("values"), list) else [row]
        ticker = str(row.get("ticker") or "MSTR").upper()
        if ticker and ticker != "MSTR":
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            day = iso_day(item.get("date") or item.get("t") or item.get("time"))
            mnav = parse_num(item.get("mNav") if "mNav" in item else item.get("mnav"))
            if not day or mnav is None:
                continue
            if day in seen:
                continue
            seen.add(day)
            try:
                day_date = date.fromisoformat(day)
            except ValueError:
                continue
            source = "official" if day_date >= MNAV_DEFINITION_CHANGE else "backcast"
            points.append({"t": day, "mnav": mnav, "source": source})
    points.sort(key=lambda p: p["t"])
    return points


def merge_live_mnav(series: list[dict[str, Any]], live_mnav: float | None, as_of: date) -> list[dict[str, Any]]:
    if live_mnav is None:
        return series
    day = as_of.isoformat()
    source = "official-live" if as_of >= MNAV_DEFINITION_CHANGE else "reconstructed"
    out = [p for p in series if p["t"] != day]
    out.append({"t": day, "mnav": live_mnav, "source": source})
    out.sort(key=lambda p: p["t"])
    return out


def _load_history_series(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        prev = json.loads(path.read_text(encoding="utf-8"))
        data = prev.get("data") if isinstance(prev, dict) else None
        if isinstance(data, dict) and isinstance(data.get("series"), list):
            return list(data["series"])
    except Exception:
        return []
    return []


def _merge_series(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_t: dict[str, dict[str, Any]] = {}
    rank = {"reconstructed": 1, "backcast": 2, "official": 3, "official-live": 4}
    for group in groups:
        for point in group:
            day = str(point.get("t") or "")
            if not day:
                continue
            src = str(point.get("source") or "reconstructed")
            slim = {"t": day, "mnav": point["mnav"], "source": src}
            prev = by_t.get(day)
            if prev is None or rank.get(src, 0) >= rank.get(str(prev.get("source")), 0):
                by_t[day] = slim
    return [by_t[k] for k in sorted(by_t)]


def _write_history(
    path: Path,
    latest: dict[str, Any],
    series: list[dict[str, Any]],
    fetched_at: str,
) -> None:
    history = {
        "fetched_at": fetched_at,
        "source": ORIGIN,
        "mnav_definition": latest["mnav_definition"],
        "mnav_definition_change_date": MNAV_DEFINITION_CHANGE.isoformat(),
        "series": series,
    }
    existing = {}
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(prev, dict) and isinstance(prev.get("data"), dict):
                existing = prev["data"]
        except Exception:
            existing = {}
    if existing.get("reconstruction"):
        history["reconstruction"] = existing["reconstruction"]
    write_json(path, envelope(history))


def fetch_kpis() -> tuple[Any, Any, Any, Any]:
    log("GET /btc/bitcoinKpis")
    btc = http_get_json(f"{API}/btc/bitcoinKpis", timeout=30)
    log("GET /btc/mstrKpiData")
    mstr = http_get_json(f"{API}/btc/mstrKpiData", timeout=30)
    log("GET /btc/mstrOptionsData")
    options = http_get_json(f"{API}/btc/mstrOptionsData", timeout=30)
    credit: Any = {}
    try:
        log("GET /v2/btc/credit")
        credit = http_get_json(f"{API}/v2/btc/credit", timeout=30)
    except Exception as exc:
        log(f"  credit skipped: {exc}")
    return btc, mstr, options, credit


def _iter_history_windows(end: date) -> list[tuple[date, date]]:
    """Newest-first ~100-day windows. Official mNav currently starts mid-2026."""
    windows: list[tuple[date, date]] = []
    stop = end
    while stop >= HISTORY_FROM:
        start = max(HISTORY_FROM, date.fromordinal(stop.toordinal() - 99))
        windows.append((start, stop))
        if start <= HISTORY_FROM:
            break
        stop = date.fromordinal(start.toordinal() - 1)
    return windows


def fetch_mnav_history(end: date) -> list[dict[str, Any]]:
    """Official timeSeries omits mNav on older rows; stop after empty windows."""
    merged: dict[str, dict[str, Any]] = {}
    empty_windows = 0
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for start, stop in _iter_history_windows(end):
        qs = urllib.parse.urlencode(
            {
                "from": start.isoformat(),
                "to": stop.isoformat(),
                "tickers": "MSTR",
                "metrics": "mNav",
            }
        )
        url = f"{API}/btc/timeSeries?{qs}"
        log(f"GET /btc/timeSeries mNav {start} → {stop}")
        payload = http_get_json(url, timeout=120)
        points = extract_mnav_series(payload)
        log(f"  chunk points with mNav: {len(points)}")
        if not points:
            empty_windows += 1
            if empty_windows >= 2:
                log("  two empty windows, stop")
                break
        else:
            empty_windows = 0
            for point in points:
                merged[point["t"]] = point
        time.sleep(0.25)

    series = [merged[k] for k in sorted(merged)]
    log(f"  history points: {len(series)}")
    if series:
        (CACHE_DIR / "timeSeries_mnav.json").write_text(
            json.dumps(series, ensure_ascii=False, indent=2)[:2_000_000],
            encoding="utf-8",
        )
    return series


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full-history",
        action="store_true",
        help="Re-fetch official timeSeries mNAV and merge onto reconstructed points",
    )
    parser.add_argument(
        "--skip-history",
        action="store_true",
        help="Deprecated alias: incremental live merge (now the default)",
    )
    parser.add_argument(
        "--keep-history-file",
        action="store_true",
        help="Do not touch mnav_history.json (still writes latest + snapshot)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    OUTPUT_JSON.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    fetched_at = now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    today = local_today()

    btc, mstr, options, credit = fetch_kpis()
    latest = build_latest(btc, mstr, options, credit, fetched_at)
    write_json(OUTPUT_JSON / "latest.json", envelope(latest))
    write_json(SNAPSHOT_DIR / f"{today.isoformat()}.json", envelope(latest))
    log(f"wrote latest.json  mNAV={latest['btc'].get('mnav')}  price={latest['mstr'].get('price')}")
    log(f"wrote snapshots/{today.isoformat()}.json")

    history_path = OUTPUT_JSON / "mnav_history.json"
    if args.keep_history_file:
        log("kept existing mnav_history.json")
    elif args.full_history:
        official = fetch_mnav_history(today)
        official = [
            p
            for p in official
            if date.fromisoformat(p["t"]) >= MNAV_DEFINITION_CHANGE
        ]
        existing = _load_history_series(history_path)
        reconstructed = [p for p in existing if p.get("source") == "reconstructed"]
        series = _merge_series(reconstructed, official)
        series = merge_live_mnav(series, latest["btc"].get("mnav"), today)
        _write_history(history_path, latest, series, fetched_at)
        official_n = sum(1 for p in series if str(p.get("source", "")).startswith("official"))
        recon_n = sum(1 for p in series if p.get("source") == "reconstructed")
        log(f"wrote mnav_history.json points={len(series)} official={official_n} reconstructed={recon_n}")
    else:
        existing = _load_history_series(history_path)
        if not existing:
            log("ERROR: mnav_history.json missing; run 2.backfill_mnav.py or 1.download.py --full-history once")
            return 1
        series = merge_live_mnav(existing, latest["btc"].get("mnav"), today)
        _write_history(history_path, latest, series, fetched_at)
        log(f"wrote mnav_history.json points={len(series)} (live merge only)")

    n_solvency = write_solvency_history(latest, today, fetched_at)
    wipeout = (latest.get("solvency") or {}).get("wipeout_px")
    log(f"wrote net_reserve_history.json points={n_solvency} wipeout={wipeout}")

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
