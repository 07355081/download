"""Download Coinglass macro indicators -> cache/coinglass/ + output/csv/coinglass/indicators.csv

Usage:
    python download_coinglass.py [--force] [--recheck-days 2] [--api-key XXX]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib import error, request

API_BASE = "https://open-api-v4.coinglass.com"
USER_AGENT = "btc-index-coinglass/1.0"

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = SCRIPT_DIR / "output" / "csv" / "coinglass" / "indicators.csv"
DEFAULT_CACHE_DIR = SCRIPT_DIR / "cache" / "coinglass"
LEGACY_CACHE_DIR = SCRIPT_DIR / "cache"

CSV_HEADER = ["endpoint", "kind", "timestamp_ms", "date", "group", "field", "value"]

DEFAULT_RECHECK_DAYS = 2
DAY_MS = 24 * 60 * 60 * 1000

ENDPOINTS: list[tuple[str, str, str]] = [
    ("ahr999", "/api/index/ahr999", "series"),
    ("puell-multiple", "/api/index/puell-multiple", "series"),
    ("fear-greed-history", "/api/index/fear-greed-history", "series"),
    ("200-week-moving-average-heatmap", "/api/index/200-week-moving-average-heatmap", "series"),
    ("bull-market-peak-indicator", "/api/bull-market-peak-indicator", "snapshot"),
    ("bitcoin-sth-sopr", "/api/index/bitcoin-sth-sopr", "series"),
    ("bitcoin-lth-sopr", "/api/index/bitcoin-lth-sopr", "series"),
    ("bitcoin-sth-realized-price", "/api/index/bitcoin-sth-realized-price", "series"),
    ("bitcoin-lth-realized-price", "/api/index/bitcoin-lth-realized-price", "series"),
    ("bitcoin-short-term-holder-supply", "/api/index/bitcoin-short-term-holder-supply", "series"),
    ("bitcoin-long-term-holder-supply", "/api/index/bitcoin-long-term-holder-supply", "series"),
    ("bitcoin-rhodl-ratio", "/api/index/bitcoin-rhodl-ratio", "series"),
    ("bitcoin-net-unrealized-profit-loss", "/api/index/bitcoin-net-unrealized-profit-loss", "series"),
    ("bitcoin-macro-oscillator", "/api/index/bitcoin-macro-oscillator", "series"),
    ("bitcoin-profitable-days", "/api/index/bitcoin/profitable-days", "series"),
    ("bitcoin-vs-global-m2-growth", "/api/index/bitcoin-vs-global-m2-growth", "series"),
    ("bitcoin-vs-us-m2-growth", "/api/index/bitcoin-vs-us-m2-growth", "series"),
]


def ts_to_date(ts_ms: int) -> str:
    try:
        return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return ""


def read_csv_rows(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    out: list[list[str]] = []
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if header != CSV_HEADER:
                return []
            for row in reader:
                if len(row) == len(CSV_HEADER):
                    out.append(row)
    except OSError:
        return []
    return out


def write_csv_rows(path: Path, rows: list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)


def max_ts_in_rows(rows: list[list[str]]) -> int | None:
    m: int | None = None
    for r in rows:
        try:
            ts = int(r[2])
        except (IndexError, ValueError):
            continue
        if m is None or ts > m:
            m = ts
    return m


def sort_key(row: list[Any]) -> tuple[int, str, str]:
    try:
        ts = int(row[2])
    except (IndexError, ValueError):
        ts = 0
    return (ts, str(row[4]) if len(row) > 4 else "", str(row[5]) if len(row) > 5 else "")


def _row_sort_merged(r: list[str]) -> tuple[str, int, str, str]:
    try:
        ts = int(r[2])
    except (IndexError, ValueError):
        ts = 0
    ep = r[0] if r else ""
    g = r[4] if len(r) > 4 else ""
    fd = r[5] if len(r) > 5 else ""
    return ep, ts, g, fd


def write_merged_csv(out_path: Path, rows: list[list[str]]) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows, key=_row_sort_merged)
    write_csv_rows(out_path, ordered)
    return len(ordered)


def collect_endpoint_rows(cache_dir: Path, endpoint_ids: Iterable[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for eid in endpoint_ids:
        safe = eid.replace("/", "_").replace("\\", "_")
        part = read_csv_rows(cache_dir / f"{safe}.csv")
        if not part:
            continue
        part.sort(key=sort_key)
        rows.extend(part)
    return rows


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def resolve_api_key(arg_key: str | None) -> str:
    if arg_key:
        return arg_key
    load_env(SCRIPT_DIR / ".env")
    load_env(SCRIPT_DIR.parent / ".env")
    key = (os.environ.get("COINGLASS_API_KEY") or "").strip()
    if not key:
        sys.exit("ERROR: COINGLASS_API_KEY not provided.")
    return key


def http_get_json(url: str, api_key: str, retries: int = 3, sleep: float = 1.5) -> dict[str, Any]:
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        req = request.Request(
            url,
            headers={"CG-API-KEY": api_key, "accept": "application/json", "User-Agent": USER_AGENT},
        )
        try:
            with request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (error.URLError, error.HTTPError, json.JSONDecodeError, TimeoutError) as exc:
            last_err = exc
            print(f"  attempt {attempt}/{retries} failed: {exc}")
            if attempt < retries:
                time.sleep(sleep * attempt)
    raise RuntimeError(f"GET {url} failed after {retries} attempts: {last_err}")


def to_number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", ""))
        except ValueError:
            return None
    return None


_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S",
)


def normalize_ts(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        n_int = int(v)
        return n_int * 1000 if n_int < 10_000_000_000 else n_int
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            n_int = int(float(s.replace(",", "")))
            return n_int * 1000 if n_int < 10_000_000_000 else n_int
        except ValueError:
            pass
        for fmt in _DATE_FORMATS:
            try:
                dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)
            except ValueError:
                continue
    return None


def numeric_subfields(d: dict[str, Any], ignore: Iterable[str] = ()) -> list[tuple[str, float]]:
    skip = set(ignore)
    return [(k, n) for k, v in d.items() if k not in skip and (n := to_number(v)) is not None]


def emit_series_rows(endpoint: str, data: Any) -> Iterable[list[Any]]:
    if not data:
        return
    if isinstance(data, dict):
        time_list = data.get("time_list") or data.get("timestamps") or data.get("timeList")
        data_list = data.get("data_list") or data.get("values") or data.get("value_list") or data.get("value")
        price_list = data.get("price_list") or data.get("price") or data.get("prices")

        if isinstance(time_list, list) and isinstance(data_list, list):
            n = min(len(time_list), len(data_list))
            for i in range(n):
                ts = normalize_ts(time_list[i])
                if ts is None:
                    continue
                date = ts_to_date(ts)
                item = data_list[i]
                if isinstance(item, dict):
                    for k, v in numeric_subfields(item):
                        yield [endpoint, "series", ts, date, "", k, v]
                else:
                    n_v = to_number(item)
                    if n_v is not None:
                        yield [endpoint, "series", ts, date, "", "value", n_v]
                if isinstance(price_list, list) and i < len(price_list):
                    pi = price_list[i]
                    if isinstance(pi, dict):
                        for pk in ("price", "btc_price", "btcPrice", "current_value"):
                            n_p = to_number(pi.get(pk))
                            if n_p is not None:
                                yield [endpoint, "series", ts, date, "", "price", n_p]
                                break
                    else:
                        n_p = to_number(pi)
                        if n_p is not None:
                            yield [endpoint, "series", ts, date, "", "price", n_p]
            return

        for key in ("history", "list", "data"):
            inner = data.get(key)
            if isinstance(inner, list):
                yield from emit_series_rows(endpoint, inner)
                return

    if isinstance(data, list):
        ts_keys = ("timestamp", "time", "date", "datetime", "date_string", "dateStr", "day", "ts")
        for entry in data:
            if not isinstance(entry, dict):
                continue
            ts = None
            for k in ts_keys:
                ts = normalize_ts(entry.get(k))
                if ts is not None:
                    break
            if ts is None:
                continue
            date = ts_to_date(ts)
            for k, v in numeric_subfields(entry, ignore=ts_keys):
                yield [endpoint, "series", ts, date, "", k, v]


def emit_snapshot_rows(endpoint: str, data: Any, fetch_ts_ms: int) -> Iterable[list[Any]]:
    fetch_date = ts_to_date(fetch_ts_ms)

    def emit_one(group: str, d: dict[str, Any]) -> Iterable[list[Any]]:
        for k, v in d.items():
            if isinstance(v, bool):
                yield [endpoint, "snapshot", fetch_ts_ms, fetch_date, group, k, "true" if v else "false"]
            elif isinstance(v, (int, float, str)):
                yield [endpoint, "snapshot", fetch_ts_ms, fetch_date, group, k, v]

    if isinstance(data, list):
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            group = str(item.get("name") or item.get("indicator") or item.get("indicator_name") or item.get("title") or i)
            yield from emit_one(group, item)
        return
    if isinstance(data, dict):
        yield from emit_one("", data)


def cache_path_for(endpoint: str, cache_dir: Path) -> Path:
    safe = endpoint.replace("/", "_").replace("\\", "_")
    return cache_dir / f"{safe}.csv"


def read_endpoint_cache(endpoint: str, cache_dir: Path) -> list[list[str]]:
    """Read per-endpoint cache; fall back to legacy cache/ root if needed."""
    rows = read_csv_rows(cache_path_for(endpoint, cache_dir))
    if rows:
        return rows
    return read_csv_rows(cache_path_for(endpoint, LEGACY_CACHE_DIR))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Merged CSV path")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help="Per-endpoint cache directory")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--recheck-days", type=int, default=DEFAULT_RECHECK_DAYS)
    parser.add_argument("--no-merge", action="store_true")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--retry", type=int, default=3)
    parser.add_argument("--sleep", type=float, default=0.6)
    args = parser.parse_args()

    api_key = resolve_api_key(args.api_key)
    out_path = Path(args.out).resolve()
    cache_dir = Path(args.cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    recheck_window_ms = max(0, args.recheck_days) * DAY_MS

    fetch_ts_ms = int(time.time() * 1000)
    fetched_count = skipped_count = 0
    failed: list[str] = []

    for i, (eid, path, kind) in enumerate(ENDPOINTS, 1):
        url = API_BASE + path
        cache_path = cache_path_for(eid, cache_dir)

        if args.force:
            existing_rows: list[list[str]] = []
            existing_max_ts = None
        else:
            existing_rows = read_endpoint_cache(eid, cache_dir)
            existing_max_ts = max_ts_in_rows(existing_rows) if existing_rows else None

        status = ""
        if existing_max_ts is not None:
            status = f"  [cache up to {ts_to_date(existing_max_ts)}, +{len(existing_rows)} rows]"
        print(f"[{i:02d}/{len(ENDPOINTS)}] {eid}  ({kind}){status}")

        try:
            payload = http_get_json(url, api_key, retries=args.retry)
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED: {exc}")
            failed.append(eid)
            continue

        data = payload.get("data")
        if data is None:
            print("  empty data, skipped")
            failed.append(eid)
            continue

        if kind == "series":
            new_rows = list(emit_series_rows(eid, data))
            if not new_rows:
                print("  no parseable rows, skipped")
                failed.append(eid)
                time.sleep(args.sleep)
                continue

            if existing_max_ts is None or args.force:
                merged, added = new_rows, len(new_rows)
                replaced_window_start = None
            else:
                cutoff = existing_max_ts - recheck_window_ms
                replaced_window_start = cutoff
                kept = [r for r in existing_rows if int(r[2]) < cutoff]
                fresh = [r for r in new_rows if int(r[2]) >= cutoff]
                merged = kept + fresh
                added = sum(1 for r in fresh if int(r[2]) > existing_max_ts)

            merged.sort(key=sort_key)
            write_csv_rows(cache_path, merged)
            new_max_ts = max_ts_in_rows(merged)
            if added == 0 and existing_max_ts is not None:
                if replaced_window_start is not None:
                    print(f"  no new days; refreshed since {ts_to_date(replaced_window_start)} (latest {ts_to_date(new_max_ts)})")
                else:
                    print(f"  no new days (latest {ts_to_date(new_max_ts)})")
                skipped_count += 1
            else:
                print(f"  +{added} new rows  (total: {len(merged)}, latest {ts_to_date(new_max_ts)})")
                fetched_count += 1
        else:
            new_rows = list(emit_snapshot_rows(eid, data, fetch_ts_ms))
            if not new_rows:
                failed.append(eid)
                time.sleep(args.sleep)
                continue
            new_rows.sort(key=sort_key)
            write_csv_rows(cache_path, new_rows)
            print(f"  snapshot replaced ({len(new_rows)} rows)")
            fetched_count += 1

        time.sleep(args.sleep)

    if args.no_merge:
        print(f"\nDONE. cache -> {cache_dir}")
    else:
        ids = [eid for eid, _, _ in ENDPOINTS]
        rows = collect_endpoint_rows(cache_dir, ids)
        total = write_merged_csv(out_path, rows)
        print(f"\nDONE. fetched={fetched_count}, up-to-date={skipped_count}, failed={len(failed)}")
        print(f"      merged {total} rows -> {out_path}")
    if failed:
        print(f"WARNING: failed: {', '.join(failed)}")
        sys.exit(2)


if __name__ == "__main__":
    main()
