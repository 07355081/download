"""Fetch CEX total assets from DefiLlama /protocol/{slug} and write daily wide CSVs.

USD series:
  - Source: payload["tvl"][].totalLiquidityUSD
  - Output: cache/cex_total_assets_daily_wide.csv

Coin-balance series (same HTTP response, no extra domain / no double fetch):
  BTC  — native BTC only (prefer chainTvls.Bitcoin.tokens, else top-level tokens.BTC)
         NOT WBTC/BTCB/FBTC. Units: BTC amount.
  ETH  — wrapped-inclusive sum: ETH + WETH + STETH + WEETH + METH + CMETH
         from top-level tokens[]. Units: ETH-equivalent amount.
  Stablecoins — whitelist sum (USDT/USDC/FDUSD/…); units ≈ USD face value.
         Excludes junk labels like RODAI. See STABLECOIN_SYMBOLS.

Missing series for a venue/day → empty cell (never coerce to 0).

Caveats:
  - DefiLlama CEX balances are address-label / custody heuristics, not PoR and not
    equal to customer liabilities.
  - Coverage and labeling differ by venue.
"""
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

from _paths import (
    ASSET_WIDE_CSV,
    BTC_BALANCE_CSV,
    ETH_BALANCE_CSV,
    STABLECOIN_BALANCE_CSV,
    ensure_cache_layout,
)

WIDE_CSV_PATH = ASSET_WIDE_CSV
BTC_BALANCE_CSV_PATH = BTC_BALANCE_CSV
ETH_BALANCE_CSV_PATH = ETH_BALANCE_CSV
STABLECOIN_BALANCE_CSV_PATH = STABLECOIN_BALANCE_CSV

REQUEST_TIMEOUT = 90
MAX_ATTEMPTS = 4
EXCHANGE_RENAMES = {
    "Binance CEX": "Binance",
    "Crypto-com": "Crypto.com",
}

# ETH wrapped-inclusive basket (exclude ETHFI / AETH* trading products).
ETH_AGGREGATE_SYMBOLS = ("ETH", "WETH", "STETH", "WEETH", "METH", "CMETH")

# Major USD-ish stables only (exact symbol match on top-level tokens).
STABLECOIN_SYMBOLS = (
    "USDT",
    "USDC",
    "FDUSD",
    "USDE",
    "TUSD",
    "DAI",
    "PYUSD",
    "USD1",
    "BUSD",
    "USDD",
    "USDP",
    "FRAX",
    "USDS",
    "GUSD",
    "LUSD",
    "USDT0",
    "USDG",
    "RLUSD",
)


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


def normalize_token_sum_rows(
    token_series: list[dict[str, Any]] | None,
    symbols: tuple[str, ...] | set[str],
) -> list[dict[str, Any]]:
    """Sum selected token amounts per UTC day (max timestamp wins). No match → skip day."""
    wanted = set(symbols)
    by_date: dict[str, dict[str, Any]] = {}
    if not isinstance(token_series, list):
        return []
    for item in token_series:
        if not isinstance(item, dict):
            continue
        ts = item.get("date")
        tokens = item.get("tokens")
        if ts is None or not isinstance(tokens, dict):
            continue
        total = 0.0
        matched = False
        for sym in wanted:
            if sym not in tokens or tokens.get(sym) is None:
                continue
            try:
                total += float(tokens[sym])
                matched = True
            except (TypeError, ValueError):
                continue
        if not matched:
            continue
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
        row = {
            "date": dt.isoformat(),
            "timestamp_utc": int(ts),
            "balance": total,
        }
        existing = by_date.get(row["date"])
        if existing is None or row["timestamp_utc"] > existing["timestamp_utc"]:
            by_date[row["date"]] = row
    return sorted(by_date.values(), key=lambda x: x["timestamp_utc"])


def normalize_token_balance_rows(
    token_series: list[dict[str, Any]] | None,
    token_symbol: str,
) -> list[dict[str, Any]]:
    return normalize_token_sum_rows(token_series, (token_symbol,))


def extract_btc_balance_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Native BTC only: Bitcoin-chain tokens first, else top-level tokens.BTC."""
    chain_tvls = payload.get("chainTvls")
    bitcoin = chain_tvls.get("Bitcoin") if isinstance(chain_tvls, dict) else None
    if isinstance(bitcoin, dict):
        rows = normalize_token_balance_rows(bitcoin.get("tokens"), "BTC")
        if rows:
            return rows
    return normalize_token_balance_rows(payload.get("tokens"), "BTC")


def extract_eth_balance_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Wrapped-inclusive ETH basket from top-level tokens (multi-chain CEX wallets)."""
    return normalize_token_sum_rows(payload.get("tokens"), ETH_AGGREGATE_SYMBOLS)


def extract_stablecoin_balance_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Whitelist USD-stable sum from top-level tokens (≈ face-value USD units)."""
    return normalize_token_sum_rows(payload.get("tokens"), STABLECOIN_SYMBOLS)


def canonical_exchange_name(name: str) -> str:
    return EXCHANGE_RENAMES.get(name, name)


def iter_exchange_series(
    ex: ExchangeSpec,
) -> tuple[str, dict[str, list[dict[str, Any]]]]:
    """One /protocol fetch → USD + BTC/ETH/stablecoin balance series."""
    payload = fetch_protocol_payload(ex.slug)
    name = canonical_exchange_name(str(payload.get("name") or ex.slug))

    usd_out: list[dict[str, Any]] = []
    tvl_rows = payload.get("tvl")
    if isinstance(tvl_rows, list):
        for r in normalize_tvl_rows(tvl_rows):
            usd_out.append(
                {
                    "exchange": name,
                    "date": r["date"],
                    "value": r["total_asset_usd"],
                }
            )

    series = {
        "usd": usd_out,
        "btc": [
            {"exchange": name, "date": r["date"], "value": r["balance"]}
            for r in extract_btc_balance_rows(payload)
        ],
        "eth": [
            {"exchange": name, "date": r["date"], "value": r["balance"]}
            for r in extract_eth_balance_rows(payload)
        ],
        "stablecoins": [
            {"exchange": name, "date": r["date"], "value": r["balance"]}
            for r in extract_stablecoin_balance_rows(payload)
        ],
    }
    return name, series


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
        for d in sorted(by_date.keys()):
            row: dict[str, Any] = {"date": d}
            values = by_date[d]
            for ex in exchanges:
                # Missing → empty string (JSON converter maps to null). Never invent 0.
                row[ex] = values.get(ex, "")
            w.writerow(row)


def filter_rows_for_incremental(
    rows: list[dict[str, Any]],
    *,
    exchange_name: str,
    last_date: date | None,
    existing_exchange_set: set[str],
) -> list[dict[str, Any]]:
    is_new_exchange_column = exchange_name not in existing_exchange_set
    if last_date is not None and not is_new_exchange_column:
        rows = [
            r for r in rows
            if datetime.strptime(r["date"], "%Y-%m-%d").date() > last_date
        ]
    if is_new_exchange_column and rows:
        print(f"  [{exchange_name}] new column detected, backfilling full history ({len(rows)} rows)")
    return rows


def merge_rows_into_wide(
    existing_by_date: dict[str, dict[str, float]],
    existing_exchanges: list[str],
    new_rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, float]], list[str]]:
    exchanges = set(existing_exchanges)
    for r in new_rows:
        d = r["date"]
        ex = r["exchange"]
        val = float(r["value"])
        exchanges.add(ex)
        slot = existing_by_date.setdefault(d, {})
        slot[ex] = val
    return existing_by_date, sorted(exchanges)


def update_wide_csv(
    *,
    label: str,
    path: Path,
    new_rows: list[dict[str, Any]],
    existing_by_date: dict[str, dict[str, float]],
    existing_exchanges: list[str],
    last_date: date | None,
) -> None:
    if not new_rows:
        if last_date is None and not existing_by_date:
            print(f"[{label}] No data found. Nothing to write.")
            return
        print(f"[{label}] No new dates found. Rewriting existing CSV with canonical headers only.")
    else:
        existing_by_date, ordered = merge_rows_into_wide(existing_by_date, existing_exchanges, new_rows)
        existing_exchanges = ordered

    ordered_exchanges = sorted(set(existing_exchanges))
    write_wide_csv(existing_by_date, ordered_exchanges, path)
    print(f"[{label}] Saved wide CSV: {path}")
    print(f"[{label}] Total dates: {len(existing_by_date)}, added rows: {len(new_rows)}")


def main() -> None:
    ensure_cache_layout()
    targets: list[tuple[str, Path]] = [
        ("usd", WIDE_CSV_PATH),
        ("btc", BTC_BALANCE_CSV_PATH),
        ("eth", ETH_BALANCE_CSV_PATH),
        ("stablecoins", STABLECOIN_BALANCE_CSV_PATH),
    ]
    state: dict[str, tuple[dict[str, dict[str, float]], list[str], date | None]] = {}
    for label, path in targets:
        by_date, exchanges, last = read_existing_wide(path)
        state[label] = (by_date, exchanges, last)
        if last is None:
            print(f"[{label}] Existing CSV not found. Will build from full history.")
        else:
            print(f"[{label}] Existing data until: {last.isoformat()}")

    new_rows: dict[str, list[dict[str, Any]]] = {label: [] for label, _ in targets}
    failed_exchanges: list[str] = []

    for ex in EXCHANGES:
        try:
            name, series = iter_exchange_series(ex)
        except Exception as exc:  # noqa: BLE001
            print(f"{ex.slug}: [skip] fetch failed: {exc}")
            failed_exchanges.append(ex.slug)
            continue

        parts: list[str] = []
        for label, _path in targets:
            _by_date, exchanges, last = state[label]
            kept = filter_rows_for_incremental(
                series[label],
                exchange_name=name,
                last_date=last,
                existing_exchange_set=set(exchanges),
            )
            new_rows[label].extend(kept)
            parts.append(f"{label} +{len(kept)}/{len(series[label])}")
        print(f"{ex.slug} ({name}): " + ", ".join(parts))

    if failed_exchanges:
        print(f"[warn] {len(failed_exchanges)} exchange(s) skipped due to errors: {failed_exchanges}")

    for label, path in targets:
        by_date, exchanges, last = state[label]
        update_wide_csv(
            label=label,
            path=path,
            new_rows=new_rows[label],
            existing_by_date=by_date,
            existing_exchanges=exchanges,
            last_date=last,
        )


if __name__ == "__main__":
    main()
