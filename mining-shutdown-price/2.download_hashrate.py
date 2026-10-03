"""Step 2/4 — Download historical chain inputs for shutdown price computation.

Next: 3.compute_shutdown.py → 4.csv_to_json.py.

BTC chain metrics (hashrate / fee-in-reward / blocks-per-day): mempool.space REST
(primary; VPS-friendly). BitInfoCharts multi-coin hashrate is best-effort only
(Cloudflare often 403s datacenter IPs). 2Miners covers altcoin hashrate.

Outputs:
  - output/json/bitinfocharts_hashrate_history.json   (optional multi-coin)
  - output/csv/bitinfocharts_hashrate_history.csv
  - output/csv/bitinfocharts_btc_hashrate_hs.csv      (from mempool; filename kept)
  - output/csv/bitinfocharts_btc_fee_in_reward_pct.csv
  - output/csv/bitinfocharts_btc_blocks_per_day.csv
  - output/json/bitinfocharts_btc_main_snapshot.json
  - output/json/2miners_<coin>_history.json
  - output/csv/2miners_<coin>_hashrate.csv
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import re
from pathlib import Path
from typing import Any, Iterable

import requests

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"
CSV_DIR = OUTPUT_DIR / "csv"
JSON_DIR = OUTPUT_DIR / "json"

# BitInfoCharts config
BITINFO_COINS: list[str] = ["btc", "zec", "ltc", "bch", "xmr", "dash", "etc", "bsv"]
BITINFO_BASE_URL = "https://bitinfocharts.com/comparison/hashrate-{}.html"
BITINFO_BTC_URL = "https://bitinfocharts.com/bitcoin/"
BITINFO_CHART_URL = "https://bitinfocharts.com/comparison/bitcoin-{slug}.html"
BITINFO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}
BITINFO_JSON_PATH = JSON_DIR / "bitinfocharts_hashrate_history.json"
BITINFO_CSV_PATH = CSV_DIR / "bitinfocharts_hashrate_history.csv"
BITINFO_BTC_HASHRATE_HS_CSV = CSV_DIR / "bitinfocharts_btc_hashrate_hs.csv"
BITINFO_BTC_FEE_IN_REWARD_CSV = CSV_DIR / "bitinfocharts_btc_fee_in_reward_pct.csv"
BITINFO_BTC_BLOCKS_PER_DAY_CSV = CSV_DIR / "bitinfocharts_btc_blocks_per_day.csv"
BITINFO_BTC_MAIN_SNAPSHOT_JSON = JSON_DIR / "bitinfocharts_btc_main_snapshot.json"
BITINFO_CSV_FIELDS = ["date", *BITINFO_COINS]

# mempool.space — VPS-reachable replacement for BitInfoCharts BTC charts
MEMPOOL_HASHRATE_URL = "https://mempool.space/api/v1/mining/hashrate/all"
MEMPOOL_REWARDS_URL = "https://mempool.space/api/v1/mining/blocks/rewards/all"
MEMPOOL_FEES_URL = "https://mempool.space/api/v1/mining/blocks/fees/all"
BLOCKCHAIN_STATS_URL = "https://api.blockchain.info/stats"
DEFAULT_BLOCKS_PER_DAY = 144.0

# 2Miners config
SUPPORTED_2MINERS_COINS = ["bch", "etc", "kas", "ckb", "zec"]
MINERS2_API_URL_TEMPLATE = "https://hr.2miners.com/api/v1/hashrate/1d/{coin}"
MINERS2_CSV_FIELDS = [
    "timestamp",
    "datetime",
    "difficulty",
    "blocktime",
    "price",
    "hashrate_hs",
    "hashrate_ths",
]


# --- BitInfoCharts fetch / parse (formerly _bitinfo_charts.py) ---


def fetch_page(url: str, *, timeout: int = 60) -> str:
    response = requests.get(url, headers=BITINFO_HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.text


def extract_dygraph_rows(html: str) -> list[list[Any]]:
    marker = 'new Dygraph(document.getElementById("container"),'
    pos = html.index(marker) + len(marker)
    data_start = html.index("[", pos)
    depth = 0
    for idx in range(data_start, len(html)):
        if html[idx] == "[":
            depth += 1
        elif html[idx] == "]":
            depth -= 1
            if depth == 0:
                frag = html[data_start : idx + 1]
                frag = re.sub(r'new Date\("([^"]+)"\)', r'"\1"', frag)
                return json.loads(frag)
    raise RuntimeError("Unable to locate Dygraph data array.")


def parse_chart_date(text: str) -> datetime.date:
    text = text.strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unsupported chart date: {text!r}")


def fetch_chart_series(slug: str, *, timeout: int = 60) -> list[tuple[datetime.date, float]]:
    html = fetch_page(BITINFO_CHART_URL.format(slug=slug), timeout=timeout)
    rows = extract_dygraph_rows(html)
    out: list[tuple[datetime.date, float]] = []
    for row in rows:
        if not row or row[0] in (None, ""):
            continue
        value = row[1]
        if value in (None, ""):
            continue
        try:
            out.append((parse_chart_date(str(row[0])), float(value)))
        except (ValueError, TypeError):
            continue
    out.sort(key=lambda item: item[0])
    return out


def fetch_bitcoin_main_snapshot(*, timeout: int = 60) -> dict[str, float]:
    """Scrape /bitcoin/ table: Blocks last 24h and Fee in Reward (%)."""
    html = fetch_page(BITINFO_BTC_URL, timeout=timeout)
    blocks_match = re.search(
        r"Blocks last 24h</td><td[^>]*>([\d.,]+)</td>",
        html,
        flags=re.IGNORECASE,
    )
    fee_match = re.search(
        r"Fee in Reward[\s\S]{0,200}?<td>\s*([\d.]+)\s*(?:%|</td>)",
        html,
        flags=re.IGNORECASE,
    )
    if not blocks_match or not fee_match:
        raise RuntimeError("Failed to parse Blocks last 24h or Fee in Reward from bitinfocharts.com/bitcoin/")
    return {
        "blocks_last_24h": float(blocks_match.group(1).replace(",", "")),
        "fee_in_reward_pct": float(fee_match.group(1)),
    }


def blocks_per_day_from_block_time_minutes(block_time_min: float) -> float | None:
    if block_time_min <= 0:
        return None
    return 1440.0 / block_time_min


def write_date_value_csv(path: Path, field: str, rows: list[tuple[datetime.date, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", field])
        for day, value in rows:
            writer.writerow([day.isoformat(), value])


# --- Multi-coin BitInfoCharts comparison ---


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--coins",
        default=",".join(SUPPORTED_2MINERS_COINS),
        help="Comma-separated 2Miners coins to update (subset of bch,etc,kas,ckb,zec).",
    )
    parser.add_argument(
        "--skip-btc-chain",
        action="store_true",
        help="Skip mempool.space BTC chain metrics (hashrate/fee/blocks)",
    )
    parser.add_argument(
        "--skip-bitinfocharts",
        action="store_true",
        help="Skip BitInfoCharts multi-coin hashrate scrape (often blocked on VPS)",
    )
    parser.add_argument("--skip-2miners", action="store_true", help="Skip 2Miners update")
    return parser.parse_args()


def ensure_output_dirs() -> None:
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    JSON_DIR.mkdir(parents=True, exist_ok=True)


def parse_2miners_coins(raw: str) -> list[str]:
    parts = [x.strip().lower() for x in raw.split(",") if x.strip()]
    if not parts:
        return SUPPORTED_2MINERS_COINS[:]
    invalid = [coin for coin in parts if coin not in SUPPORTED_2MINERS_COINS]
    if invalid:
        raise SystemExit(f"Unsupported 2Miners coins: {', '.join(invalid)}")
    return parts


def bitinfo_comparison_url() -> str:
    coin_slug = "-".join(BITINFO_COINS)
    return BITINFO_BASE_URL.format(coin_slug)


def bitinfo_parse_datetime(date_text: str) -> datetime.datetime:
    for fmt in ("%Y/%m/%d", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(date_text, fmt)
        except ValueError:
            continue
    return datetime.datetime.fromisoformat(date_text)


def bitinfo_build_records(rows: Iterable[list]) -> list[dict[str, float | None]]:
    records = []
    for row in rows:
        if len(row) != len(BITINFO_COINS) + 1:
            continue
        dt = bitinfo_parse_datetime(row[0]).replace(tzinfo=datetime.timezone.utc)
        record: dict[str, float | None] = {
            "timestamp": int(dt.timestamp()),
            "datetime": dt.isoformat().replace("+00:00", "Z"),
            "date": f"{dt.year}/{dt.month}/{dt.day}",
        }
        for coin, value in zip(BITINFO_COINS, row[1:]):
            record[coin] = None if value is None else float(value)
        records.append(record)
    return records


def bitinfo_load_existing_records() -> list[dict[str, float | None]]:
    if not BITINFO_JSON_PATH.exists():
        return []
    try:
        payload = json.loads(BITINFO_JSON_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    records = payload.get("records", [])
    if not isinstance(records, list):
        return []
    normalized: list[dict[str, float | None]] = []
    for item in records:
        if isinstance(item, dict) and "timestamp" in item:
            normalized.append(item)
    return normalized


def bitinfo_merge_incremental_records(
    existing_records: list[dict[str, float | None]],
    fetched_records: list[dict[str, float | None]],
) -> tuple[list[dict[str, float | None]], int]:
    if not existing_records:
        return fetched_records, len(fetched_records)
    latest_timestamp = max(
        int(record["timestamp"])
        for record in existing_records
        if record.get("timestamp") is not None
    )
    new_records = [
        record for record in fetched_records if int(record["timestamp"]) > latest_timestamp
    ]
    return [*existing_records, *new_records], len(new_records)


def bitinfo_save_json(records: Iterable[dict[str, float | None]], source_url: str) -> None:
    payload = {
        "source": source_url,
        "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "coins": BITINFO_COINS,
        "records": list(records),
    }
    BITINFO_JSON_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def bitinfo_save_csv(records: Iterable[dict[str, float | None]]) -> None:
    with BITINFO_CSV_PATH.open("w", newline="", encoding="utf-8") as handler:
        writer = csv.DictWriter(handler, fieldnames=BITINFO_CSV_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field) for field in BITINFO_CSV_FIELDS})


def _mempool_get_json(url: str, *, timeout: int = 120) -> Any:
    response = requests.get(url, headers=BITINFO_HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _utc_date_from_ts(ts: int | float) -> datetime.date:
    return datetime.datetime.fromtimestamp(int(ts), tz=datetime.timezone.utc).date()


def _fee_in_reward_pct(avg_fees: float, avg_rewards: float) -> float | None:
    if avg_rewards <= 0:
        return None
    pct = 100.0 * float(avg_fees) / float(avg_rewards)
    if pct < 0 or pct >= 100:
        return None
    return pct


def _align_mempool_series(
    rewards: list[dict[str, Any]],
    fees: list[dict[str, Any]],
) -> list[tuple[datetime.date, float, float]]:
    """Join rewards+fees by timestamp → (date, fee_in_reward_pct, blocks_per_day)."""
    fee_by_ts = {int(item["timestamp"]): item for item in fees if item.get("timestamp") is not None}
    rewards_sorted = sorted(
        (item for item in rewards if item.get("timestamp") is not None),
        key=lambda item: int(item["timestamp"]),
    )
    out: list[tuple[datetime.date, float, float]] = []
    prev_ts: int | None = None
    prev_height: float | None = None
    for item in rewards_sorted:
        ts = int(item["timestamp"])
        fee_item = fee_by_ts.get(ts)
        if fee_item is None:
            continue
        try:
            avg_rewards = float(item["avgRewards"])
            avg_fees = float(fee_item["avgFees"])
            avg_height = float(item["avgHeight"])
        except (KeyError, TypeError, ValueError):
            continue
        fee_pct = _fee_in_reward_pct(avg_fees, avg_rewards)
        if fee_pct is None:
            continue
        if prev_ts is None or prev_height is None:
            blocks = DEFAULT_BLOCKS_PER_DAY
        else:
            dt_days = (ts - prev_ts) / 86400.0
            height_delta = avg_height - prev_height
            if dt_days > 0 and height_delta > 0:
                blocks = height_delta / dt_days
            else:
                blocks = DEFAULT_BLOCKS_PER_DAY
        out.append((_utc_date_from_ts(ts), fee_pct, blocks))
        prev_ts = ts
        prev_height = avg_height
    return out


def update_btc_chain_metrics() -> None:
    """BTC hashrate (H/s), fee-in-reward (%), blocks/day via mempool.space (+ blockchain.info snapshot)."""
    hashrate_payload = _mempool_get_json(MEMPOOL_HASHRATE_URL)
    hashrate_points = hashrate_payload.get("hashrates") if isinstance(hashrate_payload, dict) else None
    if not isinstance(hashrate_points, list) or not hashrate_points:
        raise RuntimeError("mempool hashrate payload missing hashrates[]")

    hashrate_rows: list[tuple[datetime.date, float]] = []
    for point in hashrate_points:
        try:
            ts = int(point["timestamp"])
            avg_hs = float(point["avgHashrate"])
        except (KeyError, TypeError, ValueError):
            continue
        if avg_hs <= 0:
            continue
        hashrate_rows.append((_utc_date_from_ts(ts), avg_hs))
    hashrate_rows.sort(key=lambda item: item[0])
    # One row per date (keep last if duplicates)
    hashrate_by_day = {day: value for day, value in hashrate_rows}
    hashrate_rows = sorted(hashrate_by_day.items(), key=lambda item: item[0])
    write_date_value_csv(BITINFO_BTC_HASHRATE_HS_CSV, "hashrate_hs", hashrate_rows)
    print(f"[mempool] btc hashrate rows={len(hashrate_rows)} -> {BITINFO_BTC_HASHRATE_HS_CSV.name}")

    rewards = _mempool_get_json(MEMPOOL_REWARDS_URL)
    fees = _mempool_get_json(MEMPOOL_FEES_URL)
    if not isinstance(rewards, list) or not isinstance(fees, list):
        raise RuntimeError("mempool rewards/fees payload must be list")
    aligned = _align_mempool_series(rewards, fees)
    fee_rows = [(day, fee_pct) for day, fee_pct, _blocks in aligned]
    block_rows = [(day, blocks) for day, _fee_pct, blocks in aligned]
    write_date_value_csv(BITINFO_BTC_FEE_IN_REWARD_CSV, "fee_in_reward_pct", fee_rows)
    print(f"[mempool] fee in reward rows={len(fee_rows)} -> {BITINFO_BTC_FEE_IN_REWARD_CSV.name}")
    write_date_value_csv(BITINFO_BTC_BLOCKS_PER_DAY_CSV, "blocks_per_day", block_rows)
    print(f"[mempool] blocks/day rows={len(block_rows)} -> {BITINFO_BTC_BLOCKS_PER_DAY_CSV.name}")

    latest_fee = fee_rows[-1][1] if fee_rows else None
    latest_blocks = block_rows[-1][1] if block_rows else DEFAULT_BLOCKS_PER_DAY
    blocks_last_24h = latest_blocks
    try:
        stats = _mempool_get_json(BLOCKCHAIN_STATS_URL, timeout=30)
        if isinstance(stats, dict) and isinstance(stats.get("n_blocks_mined"), (int, float)):
            blocks_last_24h = float(stats["n_blocks_mined"])
    except Exception as exc:
        print(f"[mempool] blockchain.info stats unavailable ({exc}); using latest series blocks/day")

    if latest_fee is None:
        raise RuntimeError("no fee_in_reward series to build snapshot")

    snapshot = {
        "blocks_last_24h": blocks_last_24h,
        "fee_in_reward_pct": latest_fee,
    }
    BITINFO_BTC_MAIN_SNAPSHOT_JSON.write_text(
        json.dumps(
            {
                "source": MEMPOOL_HASHRATE_URL,
                "sources": {
                    "hashrate": MEMPOOL_HASHRATE_URL,
                    "rewards": MEMPOOL_REWARDS_URL,
                    "fees": MEMPOOL_FEES_URL,
                    "stats": BLOCKCHAIN_STATS_URL,
                },
                "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                **snapshot,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        f"[mempool] main snapshot blocks_last_24h={snapshot['blocks_last_24h']} "
        f"fee_in_reward_pct={snapshot['fee_in_reward_pct']:.4f}"
    )


def update_bitinfocharts_multicoin() -> None:
    """Best-effort multi-coin hashrate scrape (not required for dashboard shutdown price)."""
    url = bitinfo_comparison_url()
    html = fetch_page(url, timeout=30)
    rows = extract_dygraph_rows(html)
    fetched_records = bitinfo_build_records(rows)
    existing_records = bitinfo_load_existing_records()
    records, new_count = bitinfo_merge_incremental_records(existing_records, fetched_records)
    bitinfo_save_json(records, url)
    bitinfo_save_csv(records)
    if existing_records:
        print(f"[bitinfocharts] appended {new_count} new records")
    else:
        print(f"[bitinfocharts] initialized with {len(records)} records")


def update_bitinfocharts() -> None:
    """Backward-compatible name: multi-coin scrape only (BTC metrics moved to mempool)."""
    update_bitinfocharts_multicoin()


# --- 2Miners ---


def miners2_fetch_history(coin: str) -> list[dict[str, float | int]]:
    url = MINERS2_API_URL_TEMPLATE.format(coin=coin)
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected payload from 2Miners API for {coin}.")
    return payload


def miners2_build_records(history: Iterable[dict[str, float | int]]) -> list[dict[str, float | None]]:
    entries = sorted(history, key=lambda entry: int(entry["timestamp"]))
    records: list[dict[str, float | None]] = []
    for entry in entries:
        ts = int(entry["timestamp"])
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        hashrate = float(entry.get("hashrate") or 0)
        hashrate_hs = hashrate if hashrate > 0 else None
        records.append(
            {
                "timestamp": ts,
                "datetime": dt,
                "difficulty": float(entry.get("difficulty") or 0),
                "blocktime": float(entry.get("block_time") or 0),
                "price": None,
                "hashrate_hs": hashrate_hs,
                "hashrate_ths": hashrate_hs / 1e12 if hashrate_hs is not None else None,
            }
        )
    return records


def miners2_load_existing_records(json_path: Path) -> list[dict[str, float | None]]:
    if not json_path.exists():
        return []
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    records = payload.get("records", [])
    if not isinstance(records, list):
        return []
    normalized: list[dict[str, float | None]] = []
    for item in records:
        if isinstance(item, dict) and item.get("timestamp") is not None:
            normalized.append(item)
    return normalized


def miners2_merge_missing_records(
    existing_records: list[dict[str, float | None]],
    fetched_records: list[dict[str, float | None]],
) -> tuple[list[dict[str, float | None]], int]:
    if not existing_records:
        return fetched_records, len(fetched_records)

    existing_by_ts = {int(record["timestamp"]): record for record in existing_records}
    added = 0
    for record in fetched_records:
        ts = int(record["timestamp"])
        if ts not in existing_by_ts:
            existing_by_ts[ts] = record
            added += 1

    merged = [existing_by_ts[ts] for ts in sorted(existing_by_ts)]
    return merged, added


def update_2miners_coin(coin: str) -> None:
    source_url = MINERS2_API_URL_TEMPLATE.format(coin=coin)
    json_path = JSON_DIR / f"2miners_{coin}_history.json"
    csv_path = CSV_DIR / f"2miners_{coin}_hashrate.csv"
    history = miners2_fetch_history(coin)
    fetched_records = miners2_build_records(history)
    existing_records = miners2_load_existing_records(json_path)
    records, added_count = miners2_merge_missing_records(existing_records, fetched_records)
    payload = {
        "source": source_url,
        "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "records": records,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handler:
        writer = csv.DictWriter(handler, fieldnames=MINERS2_CSV_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field) for field in MINERS2_CSV_FIELDS})
    if existing_records:
        print(f"[2miners:{coin}] added {added_count} missing rows, total={len(records)}")
    else:
        print(f"[2miners:{coin}] initialized with {len(records)} rows")


def main() -> None:
    args = parse_args()
    ensure_output_dirs()

    if not args.skip_btc_chain:
        update_btc_chain_metrics()

    if not args.skip_bitinfocharts:
        try:
            update_bitinfocharts_multicoin()
        except Exception as exc:
            print(f"[bitinfocharts] multi-coin scrape skipped: {exc}")

    if not args.skip_2miners:
        for coin in parse_2miners_coins(args.coins):
            update_2miners_coin(coin)

    print("DONE. datasets updated under mining-shutdown-price/output/")


if __name__ == "__main__":
    main()
