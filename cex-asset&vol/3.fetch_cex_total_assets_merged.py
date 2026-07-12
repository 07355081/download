from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


OUTPUT_DIR = Path(__file__).resolve().parent
WIDE_CSV_PATH = OUTPUT_DIR / "cex_total_assets_daily_wide.csv"

REQUEST_TIMEOUT = 90
MAX_ATTEMPTS = 4
EXCHANGE_RENAMES = {
    "Binance CEX": "Binance",
    "Crypto-com": "Crypto.com",
}


def _build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=MAX_ATTEMPTS,
        connect=MAX_ATTEMPTS,
        read=MAX_ATTEMPTS,
        status=MAX_ATTEMPTS,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": "dashboard-data/cex-asset-vol"})
    return s


SESSION = _build_session()


@dataclass(frozen=True)
class ExchangeSpec:
    slug: str  # protocol path for /protocol/{slug}


EXCHANGES: list[ExchangeSpec] = [
    ExchangeSpec(slug="binance-cex"),
    ExchangeSpec(slug="bitfinex"),
    ExchangeSpec(slug="bybit"),
    ExchangeSpec(slug="okx"),
    ExchangeSpec(slug="htx"),
    ExchangeSpec(slug="bitget"),
    ExchangeSpec(slug="gate"),
    ExchangeSpec(slug="mexc"),
    ExchangeSpec(slug="deribit"),
    ExchangeSpec(slug="kucoin"),
    ExchangeSpec(slug="crypto-com"),
    ExchangeSpec(slug="bitmart"),
    # DEX TVL (used as asset proxy)
    ExchangeSpec(slug="hyperliquid"),
    ExchangeSpec(slug="uniswap"),
]


def fetch_protocol_payload(slug: str) -> dict[str, Any]:
    url = f"https://api.llama.fi/protocol/{slug}"
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = SESSION.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                raise ValueError(f"Unexpected API response for {slug}")
            return data
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < MAX_ATTEMPTS:
                wait = min(2 ** attempt, 15)
                print(f"  [warn] {slug} attempt {attempt}/{MAX_ATTEMPTS} failed: {exc}; retry in {wait}s")
                time.sleep(wait)
    raise RuntimeError(f"fetch_protocol_payload({slug}) failed after {MAX_ATTEMPTS} attempts: {last_exc}")


def normalize_tvl_rows(tvl_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_date: dict[str, dict[str, Any]] = {}
    for item in tvl_rows:
        ts = item.get("date")
        total_asset_usd = item.get("totalLiquidityUSD")
        if ts is None or total_asset_usd is None:
            continue
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
        row = {
            "date": dt.isoformat(),
            "timestamp_utc": int(ts),
            "total_asset_usd": float(total_asset_usd),
        }
        existing = by_date.get(row["date"])
        if existing is None or row["timestamp_utc"] > existing["timestamp_utc"]:
            by_date[row["date"]] = row
    return sorted(by_date.values(), key=lambda x: x["timestamp_utc"])


def canonical_exchange_name(name: str) -> str:
    return EXCHANGE_RENAMES.get(name, name)


def iter_exchange_rows(ex: ExchangeSpec) -> list[dict[str, Any]]:
    payload = fetch_protocol_payload(ex.slug)
    name = canonical_exchange_name(str(payload.get("name") or ex.slug))
    tvl_rows = payload.get("tvl")
    if not isinstance(tvl_rows, list):
        return []
    rows = normalize_tvl_rows(tvl_rows)
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
            "exchange": name,
            "date": r["date"],
            "total_asset_usd": r["total_asset_usd"],
        }
        )
    return out


def read_existing_wide(path: Path) -> tuple[dict[str, dict[str, float]], list[str], date | None]:
    if not path.exists():
        return {}, [], None

    by_date: dict[str, dict[str, float]] = {}
    exchanges: list[str] = []
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        exchanges_raw = [x for x in fields if x != "date"]
        exchanges = sorted({canonical_exchange_name(x) for x in exchanges_raw})
        for row in reader:
            d = row.get("date")
            if not d:
                continue
            slot: dict[str, float] = {}
            for ex_raw in exchanges_raw:
                ex = canonical_exchange_name(ex_raw)
                v = row.get(ex_raw, "")
                if v == "" or v is None:
                    continue
                try:
                    slot[ex] = float(v)
                except ValueError:
                    continue
            by_date[d] = slot

    last = None
    if by_date:
        last_str = max(by_date.keys())
        last = datetime.strptime(last_str, "%Y-%m-%d").date()
    return by_date, exchanges, last


def write_wide_csv(by_date: dict[str, dict[str, float]], exchanges: list[str], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = ["date"] + exchanges
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for date in sorted(by_date.keys()):
            row = {"date": date}
            values = by_date[date]
            for ex in exchanges:
                row[ex] = values.get(ex, "")
            w.writerow(row)


def main() -> None:
    existing_by_date, existing_exchanges, last_date = read_existing_wide(WIDE_CSV_PATH)
    existing_exchange_set = set(existing_exchanges)
    if last_date is None:
        print("Existing wide CSV not found. Will build from full history.")
    else:
        print(f"Existing data until: {last_date.isoformat()}")
        today_utc = datetime.now(timezone.utc).date()
        if last_date >= today_utc:
            print("Already up-to-date. Will still normalize and rewrite CSV headers.")

    new_rows: list[dict[str, Any]] = []
    failed_exchanges: list[str] = []
    for ex in EXCHANGES:
        try:
            rows = list(iter_exchange_rows(ex))
        except Exception as exc:  # noqa: BLE001
            print(f"{ex.slug}: [skip] fetch failed: {exc}")
            failed_exchanges.append(ex.slug)
            continue
        # For newly introduced exchanges (e.g. DEX columns), backfill full history.
        sample_name = rows[0]["exchange"] if rows else ex.slug
        is_new_exchange_column = sample_name not in existing_exchange_set
        if last_date is not None and not is_new_exchange_column:
            rows = [
                r for r in rows
                if datetime.strptime(r["date"], "%Y-%m-%d").date() > last_date
            ]
        if is_new_exchange_column:
            print(f"{ex.slug}: new column detected, backfilling full history ({len(rows)} rows)")
        new_rows.extend(rows)
        print(f"{ex.slug}: +{len(rows)} new rows")

    if failed_exchanges:
        print(f"[warn] {len(failed_exchanges)} exchange(s) skipped due to errors: {failed_exchanges}")

    if not new_rows:
        if last_date is None:
            print("No new dates found. Nothing to append.")
            return
        print("No new dates found. Rewriting existing CSV with canonical headers only.")

    # Merge new rows into existing wide data
    exchanges = set(existing_exchanges)
    for r in new_rows:
        d = r["date"]
        ex = r["exchange"]
        val = float(r["total_asset_usd"])
        exchanges.add(ex)
        slot = existing_by_date.setdefault(d, {})
        slot[ex] = val

    ordered_exchanges = sorted(exchanges)
    write_wide_csv(existing_by_date, ordered_exchanges, WIDE_CSV_PATH)
    print(f"Saved wide CSV: {WIDE_CSV_PATH}")
    print(f"Total dates: {len(existing_by_date)}, added rows: {len(new_rows)}")


if __name__ == "__main__":
    main()

