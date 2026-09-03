"""Download Deribit DVOL history into local CSV files.

Outputs:
  output/csv/dvol_<SYMBOL>_<START>_<END>_<RESOLUTION>.csv
  output/summary.csv

If a CSV already exists for the symbol/resolution, only fetches rows after the last
timestamp and rewrites the file with an updated end date in the filename.

Usage:
  python download.py
  python download.py --symbol BTC --start 20210323 --end 20260528 --resolution 3600
  python download.py --target BTC:20210323:20260528:3600 --target ETH:20210323:20260528:3600
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

DERIBIT_TEST_URL = "https://test.deribit.com/api/v2/public/get_volatility_index_data"
DERIBIT_PROD_URL = "https://www.deribit.com/api/v2/public/get_volatility_index_data"

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"
CSV_DIR = OUTPUT_DIR / "csv"
SUMMARY_CSV = OUTPUT_DIR / "summary.csv"

CSV_NAME_RE = re.compile(
    r"^dvol_(?P<symbol>[A-Z0-9]+)_(?P<start>\d{8})_(?P<end>\d{8})_(?P<resolution>[A-Z0-9]+)\.csv$"
)

DEFAULT_SYMBOLS = ("BTC", "ETH")
DEFAULT_START = "20210323"
DEFAULT_RESOLUTION = "3600"


def today_yyyymmdd() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def default_targets() -> list[tuple[str, str, str, str]]:
    end = today_yyyymmdd()
    return [(symbol, DEFAULT_START, end, DEFAULT_RESOLUTION) for symbol in DEFAULT_SYMBOLS]


def dt_to_ms(dt_str: str, *, end_of_day: bool = False) -> int:
    dt = datetime.strptime(dt_str, "%Y%m%d")
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def ms_to_yyyymmdd(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y%m%d")


def ms_to_date(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return f"{dt.year}/{dt.month}/{dt.day} {dt.hour:02d}:{dt.minute:02d}"


def parse_csv_date(text: str) -> datetime:
    text = (text or "").strip()
    date_part, _, time_part = text.partition(" ")
    year, month, day = (int(part) for part in date_part.split("/"))
    hour, minute = (int(part) for part in time_part.split(":"))
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def resolution_to_seconds(resolution: str) -> int:
    if resolution == "1D":
        return 24 * 60 * 60
    return int(resolution)


def http_post_json(url: str, payload: dict[str, Any], retries: int) -> dict[str, Any]:
    last_err: Exception | None = None
    body = json.dumps(payload).encode("utf-8")
    for attempt in range(1, retries + 1):
        req = request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except (error.HTTPError, error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(min(1.5 * attempt, 10.0))
    raise RuntimeError(f"POST {url} failed after {retries} attempts: {last_err}")


def fetch_volatility_index(
    *,
    currency: str,
    start_dt: str,
    end_dt: str,
    resolution: str,
    use_testnet: bool,
    retries: int,
) -> list[tuple[int, float, float, float, float]]:
    url = DERIBIT_TEST_URL if use_testnet else DERIBIT_PROD_URL
    start_ts = dt_to_ms(start_dt)
    end_ts = dt_to_ms(end_dt, end_of_day=True)
    if start_ts >= end_ts:
        return []

    max_candles = 1000
    chunk_span_ms = max_candles * resolution_to_seconds(resolution) * 1000
    chunk_start = start_ts
    last_ts = None
    out: list[tuple[int, float, float, float, float]] = []

    while chunk_start < end_ts:
        chunk_end = min(chunk_start + chunk_span_ms, end_ts)
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "public/get_volatility_index_data",
            "params": {
                "currency": currency,
                "start_timestamp": chunk_start,
                "end_timestamp": chunk_end,
                "resolution": resolution,
            },
        }
        data = http_post_json(url, payload, retries=retries)
        if "error" in data:
            raise RuntimeError(f"Deribit API error: {data['error']}")
        candles = ((data.get("result") or {}).get("data") or [])
        for row in candles:
            if not isinstance(row, list) or len(row) < 5:
                continue
            ts = int(row[0])
            if last_ts is not None and ts <= last_ts:
                continue
            last_ts = ts
            out.append((ts, float(row[1]), float(row[2]), float(row[3]), float(row[4])))
        chunk_start = chunk_end
    return out


def write_csv(path: Path, candles: list[tuple[int, float, float, float, float]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "open", "high", "low", "close"])
        for ts, o, h, low, c in candles:
            writer.writerow([ms_to_date(ts), o, h, low, c])
    return len(candles)


def find_existing_dvol_csv(symbol: str, resolution: str) -> Path | None:
    matches = sorted(CSV_DIR.glob(f"dvol_{symbol}_*_{resolution}.csv"))
    return matches[-1] if matches else None


def load_existing_candles(csv_path: Path) -> list[tuple[int, float, float, float, float]]:
    out: list[tuple[int, float, float, float, float]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as handler:
        reader = csv.DictReader(handler)
        for row in reader:
            raw_date = (row.get("date") or "").strip()
            if not raw_date:
                continue
            ts = int(parse_csv_date(raw_date).timestamp() * 1000)
            out.append(
                (
                    ts,
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                )
            )
    out.sort(key=lambda item: item[0])
    return out


def merge_candles_by_ts(
    existing: list[tuple[int, float, float, float, float]],
    incoming: list[tuple[int, float, float, float, float]],
) -> list[tuple[int, float, float, float, float]]:
    by_ts = {item[0]: item for item in existing}
    for row in incoming:
        by_ts[row[0]] = row
    return [by_ts[ts] for ts in sorted(by_ts.keys())]


def resolve_target(
    symbol: str,
    start: str,
    end: str,
    resolution: str,
) -> tuple[str, str, str, str, Path | None]:
    existing_path = find_existing_dvol_csv(symbol, resolution)
    if existing_path:
        match = CSV_NAME_RE.match(existing_path.name)
        if match:
            start = match.group("start")
            old_end = match.group("end")
            if end <= old_end:
                return symbol, start, old_end, resolution, existing_path
    return symbol, start, end, resolution, existing_path


def download_target(
    *,
    symbol: str,
    start: str,
    end: str,
    resolution: str,
    existing_path: Path | None,
    use_testnet: bool,
    retries: int,
) -> tuple[Path, int, bool]:
    existing_candles: list[tuple[int, float, float, float, float]] = []
    fetch_start = start
    if existing_path and existing_path.is_file():
        existing_candles = load_existing_candles(existing_path)
        match = CSV_NAME_RE.match(existing_path.name)
        if match:
            start = match.group("start")
        if existing_candles:
            last_ts = existing_candles[-1][0]
            fetch_start = ms_to_yyyymmdd(last_ts + resolution_to_seconds(resolution) * 1000)

    if existing_path and existing_candles:
        match = CSV_NAME_RE.match(existing_path.name)
        if match and end <= match.group("end"):
            return existing_path, len(existing_candles), False

    fetched = fetch_volatility_index(
        currency=symbol,
        start_dt=fetch_start,
        end_dt=end,
        resolution=resolution,
        use_testnet=use_testnet,
        retries=retries,
    )
    candles = merge_candles_by_ts(existing_candles, fetched) if existing_candles else fetched
    csv_path = CSV_DIR / f"dvol_{symbol}_{start}_{end}_{resolution}.csv"
    rows = write_csv(csv_path, candles)
    if existing_path and existing_path.resolve() != csv_path.resolve() and existing_path.is_file():
        existing_path.unlink()
    return csv_path, rows, True


def parse_targets(args: argparse.Namespace) -> list[tuple[str, str, str, str]]:
    if args.target:
        out: list[tuple[str, str, str, str]] = []
        for raw in args.target:
            parts = [part.strip() for part in raw.split(":")]
            if len(parts) != 4:
                raise SystemExit(f"Invalid --target: {raw}. Expected SYMBOL:START:END:RESOLUTION")
            out.append((parts[0].upper(), parts[1], parts[2], parts[3]))
        return out

    if args.symbol:
        return [(args.symbol.upper(), args.start, args.end, args.resolution)]

    return default_targets()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default=None, help="Single symbol, e.g. BTC/ETH")
    parser.add_argument("--start", default=DEFAULT_START, help="Start date YYYYMMDD")
    parser.add_argument("--end", default=None, help="End date YYYYMMDD (default: today UTC)")
    parser.add_argument("--resolution", default=DEFAULT_RESOLUTION, choices=["1", "60", "3600", "43200", "1D"])
    parser.add_argument("--target", action="append", help="Multi target: SYMBOL:START:END:RESOLUTION")
    parser.add_argument("--testnet", action="store_true")
    parser.add_argument("--retry", type=int, default=4)
    parser.add_argument("--sleep", type=float, default=0.2, help="Sleep seconds between targets")
    args = parser.parse_args()

    if args.end is None:
        args.end = today_yyyymmdd()

    targets = parse_targets(args)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CSV_DIR.mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict[str, Any]] = []
    for i, (symbol, start, end, resolution) in enumerate(targets, 1):
        symbol, start, end, resolution, existing_path = resolve_target(symbol, start, end, resolution)
        name = f"dvol_{symbol}_{start}_{end}_{resolution}"
        print(f"[{i}/{len(targets)}] {name}")
        started = time.time()
        try:
            csv_path, rows, updated = download_target(
                symbol=symbol,
                start=start,
                end=end,
                resolution=resolution,
                existing_path=existing_path,
                use_testnet=args.testnet,
                retries=max(1, args.retry),
            )
            if updated:
                print(f"  wrote {csv_path} ({rows} rows)")
            else:
                print(f"  up-to-date {csv_path} ({rows} rows)")
            summary_rows.append(
                {
                    "symbol": symbol,
                    "start": start,
                    "end": end,
                    "resolution": resolution,
                    "ok": True,
                    "rows": rows,
                    "elapsed_s": round(time.time() - started, 2),
                    "error": "",
                }
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED: {exc}")
            summary_rows.append(
                {
                    "symbol": symbol,
                    "start": start,
                    "end": end,
                    "resolution": resolution,
                    "ok": False,
                    "rows": 0,
                    "elapsed_s": round(time.time() - started, 2),
                    "error": str(exc)[:500],
                }
            )
        time.sleep(max(0.0, args.sleep))

    with SUMMARY_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["symbol", "start", "end", "resolution", "ok", "rows", "elapsed_s", "error"],
        )
        writer.writeheader()
        writer.writerows(summary_rows)

    failed = sum(1 for row in summary_rows if not row["ok"])
    print(f"\nDONE. targets={len(summary_rows)} failed={failed}")
    print(f"csv -> {CSV_DIR}")
    print(f"summary -> {SUMMARY_CSV}")
    if failed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
