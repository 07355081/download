"""Fetch stablecoin market cap from DefiLlama into wide CSV.

Web UI: https://defillama.com/stablecoins
Per chain (all coins on chain): https://defillama.com/stablecoins/{slug}
Per coin (all chains): https://defillama.com/stablecoin/{slug}
  e.g. USD1 https://defillama.com/stablecoin/world-liberty-financial-usd

API:
  GET stablecoincharts/{chain}              -> {chain}_stablecoins
  GET stablecoincharts/all                  -> all_stablecoins
  GET stablecoincharts/all?stablecoin={id}  -> all_{coin}
  GET stablecoincharts/{chain}?stablecoin={id} -> {chain}_{coin}

Per-chain single-coin series are fetched unless --skip-breakdown.
Does not call GET /stablecoin/{id} (that payload includes every chain).

Incremental merge (same as before):
  - CSV exists -> append dates after last checkpoint per column group
  - --full -> force full refresh for selected chains/coins

Outputs:
  cache/defillama/*.json
  output/csv/stablecoins_marketcap.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "cache" / "defillama"
CSV_PATH = HERE / "output" / "csv" / "stablecoins_marketcap.csv"

API_BASE = "https://stablecoins.llama.fi/stablecoincharts"
GLOBAL_CHART_SLUG = "all"
REQUEST_TIMEOUT = 90
MAX_ATTEMPTS = 4
CHAIN_SLEEP_SEC = 0.35
COIN_SLEEP_SEC = 0.35


def _build_session() -> requests.Session:
    session = requests.Session()
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
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": "dashboard-data/stablecoin-defillama"})
    return session


SESSION = _build_session()

_DAY_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d")


def parse_day_key(day: str) -> date:
    text = day.strip()
    for fmt in _DAY_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unsupported date: {day!r}")


def normalize_day_key(day: str) -> str:
    return parse_day_key(day).isoformat()


@dataclass(frozen=True)
class ChainSpec:
    column_prefix: str
    api_slug: str


@dataclass(frozen=True)
class CoinSpec:
    """Global (all-chain) series; column is all_{column_suffix}."""

    column_suffix: str
    defillama_id: str
    label: str = ""


CHAINS: list[ChainSpec] = [
    ChainSpec("aptos", "aptos"),
    ChainSpec("arbitrum", "arbitrum"),
    ChainSpec("avalanche", "avalanche"),
    ChainSpec("base", "base"),
    ChainSpec("bsc", "bsc"),
    ChainSpec("ethereum", "ethereum"),
    ChainSpec("hyperliquid_l1", "hyperliquid"),
    ChainSpec("mantle", "mantle"),
    ChainSpec("op_mainnet", "optimism"),
    ChainSpec("plasma", "plasma"),
    ChainSpec("polygon", "polygon"),
    ChainSpec("solana", "solana"),
    ChainSpec("ton", "ton"),
    ChainSpec("tron", "tron"),
    ChainSpec("x_layer", "x-layer"),
]

# DefiLlama pegged asset ids (stablecoins.llama.fi/stablecoins)
GLOBAL_COINS: list[CoinSpec] = [
    CoinSpec("usdt", "1", "USDT"),
    CoinSpec("usdc", "2", "USDC"),
    CoinSpec("usde", "146", "USDe"),
    CoinSpec("usd1", "262", "USD1"),  # defillama.com/stablecoin/world-liberty-financial-usd
    CoinSpec("pyusd", "120", "PYUSD"),
    CoinSpec("usdg", "286", "USDG"),
    CoinSpec("rlusd", "250", "RLUSD"),
    CoinSpec("fdusd", "119", "FDUSD"),
    CoinSpec("usdd", "14", "USDD"),
    CoinSpec("tusd", "7", "TUSD"),
]

# Fetched only to build all_usds/dai (Maker DAI + Sky USDS); not written as separate columns.
USDS_DAI_PARTS: tuple[tuple[str, str], ...] = (("dai", "5"), ("usds", "209"))

def chain_total_column(prefix: str) -> str:
    return f"{prefix}_stablecoins"


def global_column(suffix: str) -> str:
    return f"all_{suffix}"


def fetch_chart(slug: str, stablecoin_id: str | None = None) -> list[dict[str, Any]]:
    url = f"{API_BASE}/{slug}"
    if stablecoin_id:
        url = f"{url}?stablecoin={stablecoin_id}"
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = SESSION.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            if not resp.content:
                return []
            data = resp.json()
            if not isinstance(data, list):
                raise ValueError(f"Unexpected API response for {slug} stablecoin={stablecoin_id!r}")
            return data
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < MAX_ATTEMPTS:
                wait = min(2**attempt, 15)
                print(
                    f"  [warn] {slug} stablecoin={stablecoin_id} "
                    f"attempt {attempt}/{MAX_ATTEMPTS}: {exc}; retry in {wait}s"
                )
                time.sleep(wait)
    raise RuntimeError(
        f"fetch_chart({slug}, stablecoin={stablecoin_id!r}) failed after {MAX_ATTEMPTS} attempts: {last_exc}"
    )


def save_cache(name: str, payload: Any) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def normalize_pegged_usd_series(chart_rows: list[dict[str, Any]]) -> dict[str, float]:
    """Map YYYY-MM-DD -> peggedUSD value, dedupe by keeping latest timestamp per day."""
    by_date: dict[str, tuple[int, float]] = {}
    for item in chart_rows:
        ts = item.get("date")
        circulating = item.get("totalCirculatingUSD")
        if ts is None or not isinstance(circulating, dict):
            continue
        value = circulating.get("peggedUSD")
        if value is None:
            continue
        ts_int = int(ts)
        day = datetime.fromtimestamp(ts_int, tz=timezone.utc).date().isoformat()
        current = by_date.get(day)
        if current is None or ts_int >= current[0]:
            by_date[day] = (ts_int, float(value))
    return {day: value for day, (_, value) in by_date.items()}


def load_csv_rows(path: Path) -> tuple[list[str], dict[str, dict[str, Any]]]:
    if not path.is_file():
        return [], {}
    with path.open("r", encoding="utf-8-sig", newline="") as handler:
        reader = csv.DictReader(handler)
        fieldnames = list(reader.fieldnames or [])
        by_time: dict[str, dict[str, Any]] = {}
        for row in reader:
            day_raw = (row.get("time") or "").strip()
            if not day_raw:
                continue
            try:
                day = normalize_day_key(day_raw)
            except ValueError:
                continue
            item: dict[str, Any] = {"time": day}
            for key, value in row.items():
                if key == "time" or value is None or str(value).strip() == "":
                    continue
                try:
                    item[key] = float(value)
                except ValueError:
                    item[key] = value
            for key in list(item.keys()):
                if key in ("all_dai", "all_usds"):
                    item.pop(key)
                elif "usds&dai" in key:
                    item[key.replace("usds&dai", "usds/dai")] = item.pop(key)
            by_time[day] = item
    if fieldnames:
        drop = {"all_dai", "all_usds"}
        fieldnames = [c.replace("usds&dai", "usds/dai") for c in fieldnames if c not in drop]
    return fieldnames, by_time


def last_date_for_column(by_time: dict[str, dict[str, Any]], column: str) -> date | None:
    dates: list[date] = []
    for day, row in by_time.items():
        value = row.get(column)
        if isinstance(value, (int, float)):
            dates.append(parse_day_key(day))
    return max(dates) if dates else None


def column_has_history(by_time: dict[str, dict[str, Any]], column: str) -> bool:
    return last_date_for_column(by_time, column) is not None


def filter_after(series: dict[str, float], last_date: date | None) -> dict[str, float]:
    if last_date is None:
        return series
    return {
        day: value
        for day, value in series.items()
        if parse_day_key(day) > last_date
    }


def merge_series_into_column(
    by_time: dict[str, dict[str, Any]],
    column: str,
    series: dict[str, float],
) -> int:
    updated = 0
    for day, value in series.items():
        row = by_time.setdefault(day, {"time": day})
        row[column] = float(value)
        updated += 1
    return updated


def build_usds_dai_series(dai_series: dict[str, float], usds_series: dict[str, float]) -> dict[str, float]:
    days = set(dai_series) | set(usds_series)
    out: dict[str, float] = {}
    for day in days:
        total = 0.0
        seen = False
        for part in (dai_series, usds_series):
            value = part.get(day)
            if value is not None:
                total += float(value)
                seen = True
        if seen:
            out[day] = total
    return out


def fetch_chain_total(chain: ChainSpec) -> dict[str, float]:
    rows = fetch_chart(chain.api_slug)
    save_cache(chain.api_slug, rows)
    return normalize_pegged_usd_series(rows)


def fetch_global_total() -> dict[str, float]:
    rows = fetch_chart(GLOBAL_CHART_SLUG)
    save_cache("all_stablecoins", rows)
    return normalize_pegged_usd_series(rows)


def fetch_global_coin(coin: CoinSpec) -> dict[str, float]:
    rows = fetch_chart(GLOBAL_CHART_SLUG, coin.defillama_id)
    save_cache(f"all_{coin.column_suffix}", rows)
    return normalize_pegged_usd_series(rows)


def fetch_global_usds_dai() -> dict[str, float]:
    parts: list[dict[str, float]] = []
    for suffix, defillama_id in USDS_DAI_PARTS:
        rows = fetch_chart(GLOBAL_CHART_SLUG, defillama_id)
        save_cache(f"all_{suffix}_for_usds_dai", rows)
        parts.append(normalize_pegged_usd_series(rows))
    return build_usds_dai_series(parts[0], parts[1])


def cross_column(prefix: str, suffix: str) -> str:
    return f"{prefix}_{suffix}"


def fetch_chain_coin(chain: ChainSpec, coin: CoinSpec) -> dict[str, float]:
    rows = fetch_chart(chain.api_slug, coin.defillama_id)
    save_cache(f"{chain.api_slug}_{coin.column_suffix}", rows)
    return normalize_pegged_usd_series(rows)


def fetch_chain_usds_dai(chain: ChainSpec) -> dict[str, float]:
    parts: list[dict[str, float]] = []
    for suffix, defillama_id in USDS_DAI_PARTS:
        rows = fetch_chart(chain.api_slug, defillama_id)
        save_cache(f"{chain.api_slug}_{suffix}_for_usds_dai", rows)
        parts.append(normalize_pegged_usd_series(rows))
        time.sleep(CHAIN_SLEEP_SEC)
    return build_usds_dai_series(parts[0], parts[1])


def ingest_column(
    by_time: dict[str, dict[str, Any]],
    column: str,
    series: dict[str, float],
    *,
    full: bool,
) -> int:
    if not series:
        return 0
    has_history = column_has_history(by_time, column)
    last_date = None if full or not has_history else last_date_for_column(by_time, column)
    filtered = filter_after(series, last_date)
    return merge_series_into_column(by_time, column, filtered)


def column_order(existing_fields: list[str], by_time: dict[str, dict[str, Any]]) -> list[str]:
    discovered = sorted({key for row in by_time.values() for key in row if key != "time"})
    ordered = ["time"]
    for key in existing_fields:
        if key != "time" and key not in ordered:
            ordered.append(key)
    for key in discovered:
        if key not in ordered:
            ordered.append(key)
    return ordered


def write_csv(path: Path, fieldnames: list[str], by_time: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [by_time[day] for day in sorted(by_time.keys(), key=parse_day_key)]
    with path.open("w", encoding="utf-8", newline="") as handler:
        writer = csv.DictWriter(fieldnames=fieldnames, f=handler, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in fieldnames})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv-out", default=str(CSV_PATH))
    parser.add_argument(
        "--only-chains",
        default="",
        help="Comma-separated chain column prefixes (e.g. ethereum,ton). Default: all chains.",
    )
    parser.add_argument(
        "--only-coins",
        default="",
        help="Comma-separated global column suffixes (e.g. usdt,usd1,stablecoins). Default: all.",
    )
    parser.add_argument(
        "--only",
        default="",
        help="Alias for --only-chains (backward compatible).",
    )
    parser.add_argument(
        "--skip-chains",
        action="store_true",
        help="Only refresh global all_* columns.",
    )
    parser.add_argument(
        "--skip-coins",
        action="store_true",
        help="Only refresh per-chain *_stablecoins columns.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Force re-download entire history for selected chains/coins.",
    )
    parser.add_argument(
        "--skip-breakdown",
        action="store_true",
        help="Skip per-chain single-coin columns ({chain}_{coin}).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    csv_out = Path(args.csv_out).resolve()
    only_chains_raw = args.only_chains or args.only
    only_chains = {part.strip() for part in only_chains_raw.split(",") if part.strip()}
    only_coins = {part.strip() for part in args.only_coins.split(",") if part.strip()}

    existing_fields, by_time = load_csv_rows(csv_out)
    if not by_time:
        print(f"No existing CSV at {csv_out}. Will backfill full DefiLlama history.")

    total_updates = 0
    failed_chains: list[str] = []
    failed_coins: list[str] = []
    failed_breakdown: list[str] = []

    if not args.skip_chains:
        selected_chains = [c for c in CHAINS if not only_chains or c.column_prefix in only_chains]
        if only_chains and not selected_chains:
            raise SystemExit("No chains selected.")
        for chain in selected_chains:
            col = chain_total_column(chain.column_prefix)
            try:
                series = fetch_chain_total(chain)
            except Exception as exc:  # noqa: BLE001
                print(f"{chain.api_slug}: [skip] fetch failed: {exc}")
                failed_chains.append(chain.api_slug)
                continue

            has_history = column_has_history(by_time, col)
            last_date = None if args.full or not has_history else last_date_for_column(by_time, col)
            if last_date is None and not has_history:
                print(f"{chain.api_slug}: no local history, backfilling full history")
            elif last_date is not None:
                print(f"{chain.api_slug}: incremental after {last_date.isoformat()}")
            else:
                print(f"{chain.api_slug}: --full refresh")

            filtered = filter_after(series, last_date)
            updated = merge_series_into_column(by_time, col, filtered)
            total_updates += updated
            print(f"{chain.api_slug}: merged {updated} day(s), +{len(filtered)} fetched day(s)")
            time.sleep(CHAIN_SLEEP_SEC)

    if not args.skip_coins:
        want_stablecoins = not only_coins or "stablecoins" in only_coins
        selected_coins = [
            c for c in GLOBAL_COINS if not only_coins or c.column_suffix in only_coins
        ]
        want_usds_dai = not only_coins or "usds/dai" in only_coins or "usds&dai" in only_coins
        if only_coins and not want_stablecoins and not selected_coins and not want_usds_dai:
            raise SystemExit("No coins selected.")

        if want_stablecoins:
            col = global_column("stablecoins")
            try:
                series = fetch_global_total()
            except Exception as exc:  # noqa: BLE001
                print(f"all_stablecoins: [skip] fetch failed: {exc}")
                failed_coins.append("stablecoins")
                series = {}
            if series:
                has_history = column_has_history(by_time, col)
                last_date = None if args.full or not has_history else last_date_for_column(by_time, col)
                filtered = filter_after(series, last_date)
                updated = merge_series_into_column(by_time, col, filtered)
                total_updates += updated
                print(f"all_stablecoins: merged {updated} day(s), +{len(filtered)} fetched day(s)")
                time.sleep(COIN_SLEEP_SEC)

        for coin in selected_coins:
            col = global_column(coin.column_suffix)
            try:
                series = fetch_global_coin(coin)
            except Exception as exc:  # noqa: BLE001
                print(f"all_{coin.column_suffix}: [skip] fetch failed: {exc}")
                failed_coins.append(coin.column_suffix)
                continue

            has_history = column_has_history(by_time, col)
            last_date = None if args.full or not has_history else last_date_for_column(by_time, col)
            filtered = filter_after(series, last_date)
            updated = merge_series_into_column(by_time, col, filtered)
            total_updates += updated
            label = coin.label or coin.column_suffix.upper()
            print(f"all_{coin.column_suffix} ({label}): merged {updated} day(s), +{len(filtered)} fetched day(s)")
            time.sleep(COIN_SLEEP_SEC)

        if want_usds_dai:
            col = global_column("usds/dai")
            try:
                merged = fetch_global_usds_dai()
            except Exception as exc:  # noqa: BLE001
                print(f"all_usds/dai: [skip] fetch failed: {exc}")
                failed_coins.append("usds/dai")
                merged = {}
            if merged:
                has_history = column_has_history(by_time, col)
                last_date = None if args.full or not has_history else last_date_for_column(by_time, col)
                filtered = filter_after(merged, last_date)
                updated = merge_series_into_column(by_time, col, filtered)
                total_updates += updated
                print(f"all_usds/dai: merged {updated} day(s), +{len(filtered)} fetched day(s)")

    if not args.skip_breakdown:
        breakdown_chains = [c for c in CHAINS if not only_chains or c.column_prefix in only_chains]
        if only_chains and not breakdown_chains:
            raise SystemExit("No chains selected.")
        breakdown_coins = [
            c for c in GLOBAL_COINS if not only_coins or c.column_suffix in only_coins
        ]
        want_cross_usds_dai = not only_coins or "usds/dai" in only_coins or "usds&dai" in only_coins
        series_per_chain = len(breakdown_coins) + (1 if want_cross_usds_dai else 0)
        print(
            f"breakdown: {len(breakdown_chains)} chain(s) x {series_per_chain} coin series"
        )
        for chain in breakdown_chains:
            for coin in breakdown_coins:
                col = cross_column(chain.column_prefix, coin.column_suffix)
                try:
                    series = fetch_chain_coin(chain, coin)
                except Exception as exc:  # noqa: BLE001
                    print(f"{col}: [skip] fetch failed: {exc}")
                    failed_breakdown.append(col)
                    time.sleep(CHAIN_SLEEP_SEC)
                    continue
                updated = ingest_column(by_time, col, series, full=args.full)
                total_updates += updated
                print(f"{col}: merged {updated} day(s)")
                time.sleep(CHAIN_SLEEP_SEC)

            if want_cross_usds_dai:
                col = cross_column(chain.column_prefix, "usds/dai")
                try:
                    series = fetch_chain_usds_dai(chain)
                except Exception as exc:  # noqa: BLE001
                    print(f"{col}: [skip] fetch failed: {exc}")
                    failed_breakdown.append(col)
                    continue
                updated = ingest_column(by_time, col, series, full=args.full)
                total_updates += updated
                print(f"{col}: merged {updated} day(s)")

    if failed_chains:
        print(f"[warn] {len(failed_chains)} chain(s) skipped: {failed_chains}")
    if failed_coins:
        print(f"[warn] {len(failed_coins)} coin(s) skipped: {failed_coins}")
    if failed_breakdown:
        print(f"[warn] {len(failed_breakdown)} breakdown series skipped: {failed_breakdown}")

    if not by_time:
        raise SystemExit("No rows to write (all fetches failed?).")

    fieldnames = column_order(existing_fields, by_time)
    write_csv(csv_out, fieldnames, by_time)
    days = sorted(by_time.keys())
    print(
        f"Saved {csv_out} rows={len(by_time)} cols={len(fieldnames)} "
        f"range={days[0]} .. {days[-1]} day_updates={total_updates}"
    )


if __name__ == "__main__":
    main()
