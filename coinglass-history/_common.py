"""Shared utilities for all coinglass-history downloaders.

Each per-endpoint subfolder imports from here. Keep this stable; bump
`SCHEMA_VERSION` if you change the cache CSV format (will invalidate caches).
"""
from __future__ import annotations

import csv
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib import error, parse, request

# ─────────────────────────── Constants ───────────────────────────

API_BASE = "https://open-api-v4.coinglass.com"
USER_AGENT = "coinglass-history-downloader/1.0"

# 14 main exchanges from dashboard's TRACKED_EXCHANGES
TRACKED_EXCHANGES: tuple[str, ...] = (
    "Binance", "Gate", "Coinbase", "Bybit", "OKX", "Bitget", "MEXC",
    "Crypto.com", "Kucoin", "HTX", "Kraken", "Bitfinex", "Upbit", "Deribit",
)

# Intervals: dashboard only uses these 5; we localize the 3 lower-frequency ones.
# Per user spec: 1d full history, 4h/1h capped at <= 1d row count (~1825 rows)
SUPPORTED_INTERVALS = ("1h", "4h", "1d")

# Per-tuple per-interval row caps (so 4h/1h <= 1d's full-history row count)
INTERVAL_MAX_ROWS = {
    "1h": 1825,   # ~76 days
    "4h": 1825,   # ~10 months
    "1d": 100000,  # effectively unlimited; full history
}

# Default trailing window for incremental "recheck" (today + yesterday)
DEFAULT_RECHECK_DAYS = 2
ETF_RECHECK_DAYS = 4  # daily ETF; skip refetch across typical weekend gaps

# Coinglass API single-request row cap
COINGLASS_MAX_LIMIT = 4500

DAY_S = 24 * 60 * 60

# Bar period for "skip fresh tuple before API" (aligned with dashboard hybrid stale logic).
INTERVAL_PERIOD_MS: dict[str, int] = {
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "12h": 12 * 60 * 60 * 1000,
    "1d": DAY_S * 1000,
    "all": 2 * DAY_S * 1000,
}

DEFAULT_WORKERS = 4

SCHEMA_VERSION = "v1"

# Top-200 includes KPEPE; some symbol APIs return upstream code=500.
# 1000PEPE / PEPE are separate entries in the same list.
PER_SYMBOL_ENDPOINT_SKIP: dict[str, frozenset[str]] = {
    "/api/futures/funding-rate/oi-weight-history": frozenset({"KPEPE"}),
}

# ─────────────────────────── Paths ───────────────────────────

ROOT_DIR = Path(__file__).resolve().parent
SYMBOLS_DIR = ROOT_DIR / "symbols"


def module_dir(module_file: str) -> Path:
    """Resolve the calling module's own directory."""
    return Path(module_file).resolve().parent


# ─────────────────────────── Env / API key ───────────────────────────

def load_env(p: Path) -> None:
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def resolve_api_key(arg_key: str | None = None) -> str:
    if arg_key:
        return arg_key
    load_env(ROOT_DIR / ".env")
    load_env(ROOT_DIR.parent / ".env")
    key = (os.environ.get("COINGLASS_API_KEY") or "").strip()
    if not key:
        sys.exit("ERROR: COINGLASS_API_KEY not provided.")
    return key


# ─────────────────────────── HTTP ───────────────────────────

def http_get_json(
    url: str,
    params: dict[str, str] | None = None,
    api_key: str | None = None,
    retries: int = 3,
    sleep: float = 1.5,
    timeout: int = 60,
) -> tuple[int, Any]:
    """Return (status_code, parsed_body_or_raw_text). 4xx returns its body as text."""
    api_key = api_key or resolve_api_key()
    headers = {
        "CG-API-KEY": api_key,
        "accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    full_url = url + ("?" + parse.urlencode(params) if params else "")
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = request.Request(full_url, headers=headers)
            with request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                return resp.getcode(), (json.loads(body) if body.strip() else None)
        except error.HTTPError as e:
            try:
                body_text = e.read().decode("utf-8", errors="replace")
            except Exception:
                body_text = ""
            return e.code, body_text  # do NOT retry 4xx
        except (error.URLError, json.JSONDecodeError, TimeoutError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(sleep * attempt)
    raise RuntimeError(f"GET {full_url} failed after {retries} attempts: {last_err}")


class ApiRequestLimiter:
    """Serialize API start times so global spacing ≈ min_interval seconds."""

    def __init__(self, min_interval: float) -> None:
        self._min_interval = max(0.0, float(min_interval))
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        if self._min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            if now < self._next_at:
                time.sleep(self._next_at - now)
            self._next_at = time.monotonic() + self._min_interval


class FailureBudget:
    def __init__(self, max_failures: int) -> None:
        self.max_failures = max(0, int(max_failures))
        self._count = 0
        self._lock = threading.Lock()

    def record(self) -> bool:
        with self._lock:
            self._count += 1
            return self._count > self.max_failures

    def exceeded(self) -> bool:
        with self._lock:
            return self._count > self.max_failures


class JobStats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.new = 0
        self.fresh = 0
        self.empty = 0
        self.fail = 0

    def inc(self, field: str, n: int = 1) -> None:
        with self._lock:
            setattr(self, field, getattr(self, field) + n)


def interval_period_ms(interval: str) -> int:
    return INTERVAL_PERIOD_MS.get(interval, 2 * DAY_S * 1000)


def is_series_fresh(latest_ms: int | None, interval: str, *, force: bool = False) -> bool:
    """True when local latest bar still covers the current interval (skip API)."""
    if force or latest_ms is None:
        return False
    return (now_ms() - latest_ms) <= interval_period_ms(interval)


def should_skip_api_fetch(
    latest_ms: int | None,
    interval: str,
    *,
    force: bool = False,
    recheck_days: int = DEFAULT_RECHECK_DAYS,
) -> bool:
    """Skip API when local data is within one bar period or recheck_days (whichever is wider)."""
    if force or latest_ms is None:
        return False
    fresh_ms = max(interval_period_ms(interval), int(recheck_days) * DAY_S * 1000)
    return (now_ms() - latest_ms) <= fresh_ms


def latest_ohlc_ts_ms(cached: dict[int, Any]) -> int | None:
    return max(cached.keys()) if cached else None


def latest_wide_ts_ms(cached: dict[tuple[int, str], float]) -> int | None:
    if not cached:
        return None
    return max(t for (t, _) in cached.keys())


def run_indexed_jobs(
    jobs: list[tuple[int, Any]],
    worker: Callable[[int, Any], dict],
    *,
    workers: int = 1,
    status_csv: Path | None = None,
    progress_hook: Callable[[list[dict]], None] | None = None,
    progress_every: int = 50,
) -> list[dict]:
    """Run (index, payload) jobs with optional thread pool; returns rows sorted by index."""
    workers = max(1, min(int(workers), 32))
    rows_by_index: dict[int, dict] = {}

    def _maybe_progress() -> None:
        if not progress_hook:
            return
        ordered = [rows_by_index[i] for i in sorted(rows_by_index)]
        if ordered and (len(ordered) % progress_every == 0):
            progress_hook(ordered)

    if workers == 1:
        for idx, payload in jobs:
            rows_by_index[idx] = worker(idx, payload)
            _maybe_progress()
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(worker, idx, payload): idx for idx, payload in jobs}
            for fut in as_completed(futures):
                rows_by_index[futures[fut]] = fut.result()
                _maybe_progress()

    result = [rows_by_index[i] for i in sorted(rows_by_index)]
    if status_csv is not None:
        write_status_csv(status_csv, result)
    return result


# ─────────────────────────── Time helpers ───────────────────────────

def now_ms() -> int:
    return int(time.time() * 1000)


def ts_to_date(ts_ms: int) -> str:
    try:
        return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return ""


def normalize_ts_ms(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        n = int(v)
        return n * 1000 if n < 10_000_000_000 else n
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            n = int(float(s.replace(",", "")))
            return n * 1000 if n < 10_000_000_000 else n
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d",
                    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
            try:
                return int(datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp() * 1000)
            except ValueError:
                continue
    return None


# ─────────────────────────── OHLC cache (per (exchange, symbol, interval) tuple) ───────────────────────────
#
# Cache file = one CSV per tuple, schema:
#     time_ms,open,high,low,close,volume_usd,extra_json
#
# extra_json carries any additional numeric fields the response had
# (e.g. open_interest, taker_buy_volume, etc.) as a compact JSON string.

OHLC_HEADER = ["time_ms", "open", "high", "low", "close", "volume_usd", "extra_json"]

# When cache/*.csv is missing, load checkpoint from sibling output/json/*.json (read-only).
JSON_CACHE_FALLBACK = os.environ.get("DATA_DOWNLOAD_JSON_CACHE_FALLBACK", "1").strip() not in (
    "0",
    "false",
    "False",
    "no",
)


def safe_filename(*parts: str) -> str:
    """Combine parts into a filesystem-safe filename (joined with `_`)."""
    out = []
    for p in parts:
        s = str(p).strip()
        for c in r'\/:*?"<>|':
            s = s.replace(c, "_")
        out.append(s)
    return "_".join(out)


def cache_path_to_json_path(cache_path: Path) -> Path | None:
    """Map module/cache/<stem>.csv -> module/output/json/<stem>.json."""
    if cache_path.suffix.lower() != ".csv":
        return None
    if cache_path.parent.name != "cache":
        return None
    module_dir = cache_path.parent.parent
    return module_dir / "output" / "json" / f"{cache_path.stem}.json"


def _load_json_file(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _ohlc_rows_to_cache_dict(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Normalize extract_ohlc_rows output to the same shape as read_ohlc_cache."""
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        try:
            ts = int(row["time_ms"])
        except (KeyError, ValueError, TypeError):
            continue
        out[ts] = {
            "time_ms": str(ts),
            "open": "" if row.get("open") is None else str(row["open"]),
            "high": "" if row.get("high") is None else str(row["high"]),
            "low": "" if row.get("low") is None else str(row["low"]),
            "close": "" if row.get("close") is None else str(row["close"]),
            "volume_usd": "" if row.get("volume_usd") is None else str(row["volume_usd"]),
            "extra_json": str(row.get("extra_json") or ""),
        }
    return out


def ohlc_cache_from_json_file(json_path: Path) -> dict[int, dict[str, Any]]:
    payload = _load_json_file(json_path)
    if not payload:
        return {}
    data = payload.get("data")
    if isinstance(data, dict) and ("time_list" in data or "data_map" in data):
        return _ohlc_rows_to_cache_dict(_wide_chart_to_ohlc_rows(data))
    if not isinstance(data, list):
        return {}
    return _ohlc_rows_to_cache_dict(extract_ohlc_rows(data))


def _wide_chart_to_ohlc_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert Coinglass {time_list, data_map} chart payload to OHLC-style rows (volume in extra)."""
    time_list = data.get("time_list") or []
    data_map = data.get("data_map") or {}
    if not isinstance(time_list, list) or not isinstance(data_map, dict):
        return []
    rows: list[dict[str, Any]] = []
    for i, ts_raw in enumerate(time_list):
        ts_ms = normalize_ts_ms(ts_raw)
        if ts_ms is None:
            continue
        extra: dict[str, Any] = {}
        for ex, series in data_map.items():
            if not isinstance(series, list) or i >= len(series):
                continue
            v = series[i]
            if v is None or v == "":
                continue
            try:
                extra[str(ex)] = float(v)
            except (ValueError, TypeError):
                continue
        if not extra:
            continue
        rows.append(
            {
                "time_ms": ts_ms,
                "open": "",
                "high": "",
                "low": "",
                "close": "",
                "volume_usd": "",
                "extra_json": json.dumps(extra, ensure_ascii=False, separators=(",", ":")),
            }
        )
    return rows


def _premium_discount_to_wide(payload: dict[str, Any]) -> dict[tuple[int, str], float]:
    """ETF premium-discount list payload -> wide cache entries."""
    out: dict[tuple[int, str], float] = {}
    data = payload.get("data")
    if not isinstance(data, list):
        return out
    preserved = ("nav_usd", "market_price_usd", "premium_discount_details")
    for item in data:
        if not isinstance(item, dict):
            continue
        ts = normalize_ts_ms(item.get("timestamp"))
        if ts is None:
            continue
        sub = item.get("list") or []
        if not isinstance(sub, list):
            continue
        for row in sub:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").strip()
            if not ticker:
                continue
            for field in preserved:
                v = row.get(field)
                if v is None or v == "":
                    continue
                try:
                    out[(ts, f"{ticker}__{field}")] = float(v)
                except (ValueError, TypeError):
                    pass
    return out


def wide_cache_from_json_file(json_path: Path) -> dict[tuple[int, str], float]:
    payload = _load_json_file(json_path)
    if not payload:
        return {}
    data = payload.get("data")
    if isinstance(data, dict) and ("time_list" in data or "data_map" in data):
        out: dict[tuple[int, str], float] = {}
        time_list = data.get("time_list") or []
        price_list = data.get("price_list") or []
        data_map = data.get("data_map") or {}
        for i, ts_raw in enumerate(time_list):
            ts = normalize_ts_ms(ts_raw)
            if ts is None:
                continue
            if isinstance(price_list, list) and i < len(price_list) and price_list[i] not in (None, ""):
                try:
                    out[(ts, "_price")] = float(price_list[i])
                except (ValueError, TypeError):
                    pass
            if isinstance(data_map, dict):
                for ex, series in data_map.items():
                    if not isinstance(series, list) or i >= len(series) or series[i] in (None, ""):
                        continue
                    try:
                        out[(ts, str(ex))] = float(series[i])
                    except (ValueError, TypeError):
                        pass
        return out
    if isinstance(data, list) and data and isinstance(data[0], dict) and "list" in data[0]:
        return _premium_discount_to_wide(payload)
    return {}


def read_ohlc_cache(p: Path) -> dict[int, dict[str, Any]]:
    """Return {time_ms: row_dict}. Empty if file missing/unreadable.

    Falls back to output/json/<same-stem>.json when CSV is absent (incremental download checkpoint).
    """
    out: dict[int, dict[str, Any]] = {}
    if p.exists():
        try:
            with p.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames and reader.fieldnames[0] == "time_ms":
                    for row in reader:
                        try:
                            t = int(row["time_ms"])
                        except (KeyError, ValueError, TypeError):
                            continue
                        out[t] = row
        except OSError:
            out = {}
    if out or not JSON_CACHE_FALLBACK:
        return out
    jp = cache_path_to_json_path(p)
    if jp and jp.is_file():
        return ohlc_cache_from_json_file(jp)
    return {}


def write_ohlc_cache(p: Path, rows_by_ts: dict[int, dict[str, Any]]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OHLC_HEADER, extrasaction="ignore")
        writer.writeheader()
        for ts in sorted(rows_by_ts.keys()):
            row = rows_by_ts[ts]
            row.setdefault("time_ms", ts)
            writer.writerow(row)


def merge_ohlc_rows(
    cached: dict[int, dict[str, Any]],
    new_rows: list[dict[str, Any]],
    recheck_days: int,
    existing_max_ms: int | None,
) -> tuple[dict[int, dict[str, Any]], int]:
    """Lossless merge: cached rows kept, new rows overwrite for same ts.
    Returns (merged_dict, count_strictly_newer_than_existing_max).
    """
    merged = dict(cached)
    added_new = 0
    for row in new_rows:
        try:
            ts = int(row["time_ms"])
        except (KeyError, ValueError, TypeError):
            continue
        if existing_max_ms is None or ts > existing_max_ms:
            added_new += 1
        merged[ts] = row
    return merged, added_new


# ─────────────────────────── Generic series-row extractor ───────────────────────────
#
# Coinglass returns time series in two common shapes:
#   (a) [{time, open, high, low, close, volume_usd, ...}, ...]  (most history endpoints)
#   (b) {time_list:[...], data_list:[...], price_list:[...]}    (some index endpoints)
#
# This function normalizes (a) into our standard {time_ms, open, high, low, close,
# volume_usd, extra_json} dicts. Per-endpoint downloaders may use this directly
# or write their own extractor for non-standard shapes.

OHLC_TIME_KEYS = ("time", "timestamp", "t", "ts", "date", "datetime", "date_string")
OHLC_NUMERIC_FIELDS = ("open", "high", "low", "close", "volume_usd")


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


def extract_ohlc_rows(payload_data: Any) -> list[dict[str, Any]]:
    """Normalize a Coinglass time-series payload to OHLC cache rows."""
    if not isinstance(payload_data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in payload_data:
        if not isinstance(item, dict):
            continue
        ts_ms: int | None = None
        for k in OHLC_TIME_KEYS:
            ts_ms = normalize_ts_ms(item.get(k))
            if ts_ms is not None:
                break
        if ts_ms is None:
            continue
        row: dict[str, Any] = {"time_ms": ts_ms}
        extra: dict[str, Any] = {}
        for k, v in item.items():
            if k in OHLC_TIME_KEYS:
                continue
            if k in OHLC_NUMERIC_FIELDS:
                f = _to_float(v)
                if f is not None:
                    row[k] = f
            else:
                f = _to_float(v)
                if f is not None:
                    extra[k] = f
                elif isinstance(v, str) and v:
                    extra[k] = v
        row["extra_json"] = json.dumps(extra, ensure_ascii=False, separators=(",", ":")) if extra else ""
        out.append(row)
    return out


# ─────────────────────────── Wide-format cache (option exchange chart endpoints) ───────────────────────────
#
# Some endpoints return {time_list, price_list, data_map: {exchange: [values]}}
# Stored in long format:  time_ms, key, value
#   - key = exchange name; or "_price" for the price_list series

WIDE_HEADER = ["time_ms", "key", "value"]


def read_wide_cache(p: Path) -> dict[tuple[int, str], float]:
    """Wide-format cache; falls back to output/json/<stem>.json when CSV missing."""
    out: dict[tuple[int, str], float] = {}
    if p.exists():
        try:
            with p.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames and reader.fieldnames[:1] == ["time_ms"]:
                    for row in reader:
                        try:
                            t = int(row["time_ms"])
                            v = float(row["value"])
                        except (ValueError, KeyError, TypeError):
                            continue
                        out[(t, row.get("key", ""))] = v
        except OSError:
            out = {}
    if out or not JSON_CACHE_FALLBACK:
        return out
    jp = cache_path_to_json_path(p)
    if jp and jp.is_file():
        return wide_cache_from_json_file(jp)
    return {}


def write_wide_cache(p: Path, data: dict[tuple[int, str], float]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=WIDE_HEADER)
        writer.writeheader()
        for (ts, key) in sorted(data.keys()):
            writer.writerow({"time_ms": ts, "key": key, "value": data[(ts, key)]})


# ─────────────────────────── Status CSV (per-task report) ───────────────────────────

STATUS_HEADER = [
    "index", "exchange", "symbol", "interval",
    "status_code", "ok", "rows_added", "cache_total",
    "latest_date", "error_body",
]


def write_status_csv(p: Path, rows: list[dict[str, Any]]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=STATUS_HEADER, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


# ─────────────────────────── Per-symbol OHLC runner ───────────────────────────
#
# Generic driver for endpoints that take only (symbol, interval) and return
# OHLC data. Each per-endpoint download.py just supplies the URL path and
# calls run_per_symbol_download(...).

def run_per_symbol_download(
    endpoint_path: str,
    market: str,                 # "futures" or "spot" — controls top-200 source
    cache_dir: Path,
    status_csv: Path,
    args: Any,                   # argparse.Namespace with shared flags
    api_key: str,
    extra_params: dict[str, str] | None = None,
    intervals_override: tuple[str, ...] | None = None,
    skip_symbols: frozenset[str] | None = None,
    symbol_scope: str = "top200",
) -> int:
    """Run incremental download per (symbol, interval) tuple.

    `extra_params` is merged into every request (e.g. exchange_list=...).
    `intervals_override` overrides SUPPORTED_INTERVALS (used when an endpoint
    has its own granularity vocabulary, e.g. range={1h,4h,12h}).
    `symbol_scope` is ``top200`` or ``all`` (Coinglass supported-coins).

    Returns process exit code (0 ok, 2 partial failures).
    """
    sym_filter = set(s.strip() for s in args.symbol.split(",")) if args.symbol else None
    base_intervals = intervals_override if intervals_override is not None else SUPPORTED_INTERVALS
    intervals = list(base_intervals) if not args.interval else [i.strip() for i in args.interval.split(",")]

    symbols = load_symbols(market, symbol_scope)
    skip = PER_SYMBOL_ENDPOINT_SKIP.get(endpoint_path, frozenset())
    if skip_symbols:
        skip = skip | skip_symbols
    if skip:
        symbols = [s for s in symbols if s not in skip]
    if sym_filter:
        symbols = [s for s in symbols if s in sym_filter]

    tuples = [(s, i) for s in symbols for i in intervals]
    if args.max_tuples and args.max_tuples > 0:
        tuples = tuples[: args.max_tuples]

    cache_dir.mkdir(parents=True, exist_ok=True)
    status_csv.parent.mkdir(parents=True, exist_ok=True)
    workers = max(1, int(getattr(args, "workers", DEFAULT_WORKERS) or DEFAULT_WORKERS))
    print(
        f"endpoint={endpoint_path}  scope={symbol_scope}  symbols={len(symbols)}  "
        f"tuples={len(tuples)}  workers={workers}  cache={cache_dir}",
        flush=True,
    )

    limiter = ApiRequestLimiter(args.sleep)
    failures = FailureBudget(args.max_failures)
    stats = JobStats()

    def _worker(idx: int, job: tuple[str, str]) -> dict:
        symbol, interval = job
        if failures.exceeded():
            return {
                "index": idx,
                "exchange": "",
                "symbol": symbol,
                "interval": interval,
                "status_code": 0,
                "ok": False,
                "skipped": True,
                "error_body": "failure budget exceeded",
            }

        cf = cache_dir / f"{safe_filename(symbol, interval)}.csv"
        cached = {} if args.force else read_ohlc_cache(cf)
        emax = max(cached.keys()) if cached else None
        latest_date = ts_to_date(emax) if emax else ""

        if is_series_fresh(emax, interval, force=args.force):
            stats.inc("fresh")
            return {
                "index": idx,
                "exchange": "",
                "symbol": symbol,
                "interval": interval,
                "status_code": 0,
                "ok": True,
                "rows_added": 0,
                "cache_total": len(cached),
                "latest_date": latest_date,
                "skipped_fresh": True,
            }

        since_ms = max(0, emax - args.recheck_days * DAY_S * 1000) if emax is not None else None
        limit = min(INTERVAL_MAX_ROWS.get(interval, COINGLASS_MAX_LIMIT), COINGLASS_MAX_LIMIT)
        params = {"symbol": symbol, "interval": interval, "limit": str(limit)}
        if extra_params:
            params.update(extra_params)
        if since_ms is not None:
            params["start_time"] = str(since_ms)

        limiter.wait()
        try:
            status, payload = http_get_json(
                API_BASE + endpoint_path, params=params, api_key=api_key, retries=args.retry
            )
        except Exception as e:
            status, payload = -1, str(e)

        row: dict[str, Any] = {
            "index": idx,
            "exchange": "",
            "symbol": symbol,
            "interval": interval,
            "status_code": status,
        }

        if status != 200 or not isinstance(payload, dict):
            stats.inc("fail")
            err = payload if isinstance(payload, str) else str(payload)[:300]
            row.update({"ok": False, "cache_total": len(cached), "error_body": err[:400]})
            if failures.record():
                print(f"\nSTOPPED: failures exceeded limit={args.max_failures}", flush=True)
            return row

        code = str(payload.get("code", ""))
        if code != "0":
            stats.inc("fail")
            row.update(
                {
                    "ok": False,
                    "cache_total": len(cached),
                    "error_body": f"upstream code={code} msg={payload.get('msg')!r}"[:400],
                }
            )
            if failures.record():
                print(f"\nSTOPPED: failures exceeded limit={args.max_failures}", flush=True)
            return row

        rows = extract_ohlc_rows(payload.get("data") or [])
        if not rows and not cached:
            stats.inc("empty")
            row.update({"ok": True, "rows_added": 0, "cache_total": 0, "latest_date": ""})
            return row

        merged, added = merge_ohlc_rows(cached, rows, args.recheck_days, emax)
        write_ohlc_cache(cf, merged)
        latest_ms = max(merged.keys()) if merged else None
        latest_date = ts_to_date(latest_ms) if latest_ms else ""
        row.update(
            {
                "ok": True,
                "rows_added": added,
                "cache_total": len(merged),
                "latest_date": latest_date,
            }
        )
        if added == 0 and emax is not None:
            stats.inc("fresh")
        else:
            stats.inc("new")
        return row

    jobs = [(idx, tup) for idx, tup in enumerate(tuples, 1)]

    def _progress(rows: list[dict]) -> None:
        write_status_csv(status_csv, rows)

    run_indexed_jobs(
        jobs,
        _worker,
        workers=workers,
        status_csv=status_csv,
        progress_hook=_progress,
        progress_every=50,
    )

    print(
        f"\nDONE.  fetched={stats.new}  skipped_fresh={stats.fresh}  "
        f"empty={stats.empty}  failed={stats.fail}",
        flush=True,
    )
    print(f"       status -> {status_csv}\n       cache  -> {cache_dir}", flush=True)
    return 2 if stats.fail else 0


def add_per_symbol_args(parser):
    parser.add_argument("--symbol", default=None, help="Comma-separated subset of top-200 symbols")
    parser.add_argument("--interval", default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--recheck-days", type=int, default=DEFAULT_RECHECK_DAYS)
    parser.add_argument("--retry", type=int, default=3)
    parser.add_argument("--sleep", type=float, default=0.25)
    parser.add_argument("--max-failures", type=int, default=50)
    parser.add_argument("--max-tuples", type=int, default=0)
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Concurrent download workers (default {DEFAULT_WORKERS})",
    )
    parser.add_argument("--api-key", default=None)


def per_symbol_csv_to_json(cache_dir: Path, json_out_dir: Path, args: Any) -> None:
    """Generic csv->json: one JSON per (symbol, interval) tuple."""
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import _json_merge as JM  # noqa: E402

    if not cache_dir.exists():
        raise SystemExit(f"cache dir not found: {cache_dir}")
    json_out_dir.mkdir(parents=True, exist_ok=True)
    sym_f = set(s.strip() for s in args.symbol.split(",")) if args.symbol else None
    iv_f = set(i.strip() for i in args.interval.split(",")) if args.interval else None

    written = 0; skipped = 0; total = 0
    for p in sorted(cache_dir.glob("*.csv")):
        if not p.name.endswith(".csv"): continue
        stem = p.name[:-4]
        if "_" not in stem: continue
        symbol, interval = stem.rsplit("_", 1)
        if interval not in SUPPORTED_INTERVALS: continue
        if sym_f and symbol not in sym_f: continue
        if iv_f and interval not in iv_f: continue
        rows = read_ohlc_cache(p)
        if not rows:
            skipped += 1; continue
        candles = []
        for ts in sorted(rows.keys()):
            r = rows[ts]
            c = {"time": ts}
            for k in ("open", "high", "low", "close", "volume_usd"):
                v = r.get(k)
                if v not in (None, ""):
                    try: c[k] = float(v)
                    except (ValueError, TypeError): pass
            extra = r.get("extra_json") or ""
            if extra:
                try:
                    eo = json.loads(extra)
                    if isinstance(eo, dict):
                        for k, v in eo.items(): c.setdefault(k, v)
                except json.JSONDecodeError: pass
            candles.append(c)
        payload = {"code": "0", "msg": "success", "symbol": symbol, "interval": interval, "data": candles}
        out = json_out_dir / f"{safe_filename(symbol, interval)}.json"
        JM.write_coinglass_candles_json(out, payload)
        sz = out.stat().st_size
        total += sz; written += 1
        if written <= 3 or written % 100 == 0:
            print(f"  wrote {out.name}  ({len(candles)} candles, {sz:,} bytes)")
    print(f"\nDONE.  wrote={written}  skipped={skipped}  total={total:,} bytes -> {json_out_dir}")


# ─────────────────────────── ETF helpers ───────────────────────────

ETF_MARKETS = ("bitcoin", "ethereum", "solana", "xrp")

# /api/etf/{market}/list — Coinglass v4 仅提供 bitcoin、ethereum（solana/xrp 返回 404）
ETF_LIST_MARKETS = ("bitcoin", "ethereum")


def fetch_etf_endpoint(
    market: str,
    sub_path: str,                # "list" | "history" | "aum" | "flow-history"
    extra_params: dict[str, str] | None = None,
    api_key: str | None = None,
    retries: int = 3,
) -> tuple[int, Any]:
    """Generic ETF GET. Returns (status, parsed payload)."""
    api_key = api_key or resolve_api_key()
    url = f"{API_BASE}/api/etf/{market}/{sub_path}"
    params = dict(extra_params or {})
    return http_get_json(url, params=params, api_key=api_key, retries=retries)


# ─────────────────────────── Symbol resolver ───────────────────────────

def load_top200(market: str, max_age_hours: int = 24 * 7) -> list[str]:
    """Load (and lazily refresh) the cached top-200 symbol list for `market`
    in {"futures", "spot"}. Refreshes via _coin_pairs.py when older than max_age_hours.
    """
    return load_symbols(market, "top200", max_age_hours=max_age_hours)


def load_symbols(market: str, scope: str = "top200", max_age_hours: int = 24 * 7) -> list[str]:
    """Load symbol list for `market` with scope ``top200`` or ``all``."""
    if market not in ("futures", "spot"):
        raise ValueError(f"unsupported market: {market}")
    if scope not in ("top200", "all"):
        raise ValueError(f"unsupported symbol scope: {scope}")

    if scope == "top200":
        from _coin_pairs import refresh_top200, top200_path  # local to avoid cycles

        p = top200_path(market)
        if (not p.exists()) or (time.time() - p.stat().st_mtime > max_age_hours * 3600):
            refresh_top200(market)
        if not p.exists():
            raise SystemExit(f"top-200 list for {market} not available at {p}")
        return json.loads(p.read_text(encoding="utf-8"))["symbols"]

    from _coin_pairs import all_supported_path, refresh_all_supported  # local to avoid cycles

    p = all_supported_path(market)
    if (not p.exists()) or (time.time() - p.stat().st_mtime > max_age_hours * 3600):
        refresh_all_supported(market)
    if not p.exists():
        raise SystemExit(f"all-supported list for {market} not available at {p}")
    return json.loads(p.read_text(encoding="utf-8"))["symbols"]


def load_instruments(market: str, max_age_hours: int = 24 * 7) -> dict[str, list[dict]]:
    """Load (and lazily refresh) the filtered instrument map for `market`.

    Returns: {exchange: [{instrument_id, base_asset, quote_asset}, ...]}

    Filter: futures = all pairs; spot = USDT/USDC/USD quote.
    """
    from _coin_pairs import instruments_path, refresh_instruments  # local to avoid cycles
    p = instruments_path(market)
    if (not p.exists()) or (time.time() - p.stat().st_mtime > max_age_hours * 3600):
        refresh_instruments(market)
    if not p.exists():
        raise SystemExit(f"instrument list for {market} not available at {p}")
    return json.loads(p.read_text(encoding="utf-8"))["by_exchange"]

