"""Step 3/4 — Compute BTC historical shutdown price from local CSV/JSON inputs.

Reads only files produced by 2.download_hashrate.py (no HTTP / scraping).

Next: 4.csv_to_json.py.

Formula (embedded from shutdown price.xlsx logic):
  fee_ratio = fee_in_reward_pct / 100
  reward_with_fees = block_reward / (1 - fee_ratio)
  network_daily_coins = blocks_per_day * reward_with_fees
  daily_electricity_usd = power_w / 1000 * electricity_usd_per_kwh * 24
  daily_btc_income = (machine_hashrate / network_hashrate) * network_daily_coins
  shutdown_price_usd = daily_electricity_usd / daily_btc_income
  energyEfficiencyRatio = power_w  (W per TH/s for 1 TH reference machine)

Inputs (from 2.download_hashrate.py):
  output/csv/bitinfocharts_btc_hashrate_hs.csv
  output/csv/bitinfocharts_btc_fee_in_reward_pct.csv
  output/csv/bitinfocharts_btc_blocks_per_day.csv
  output/json/bitinfocharts_btc_main_snapshot.json  (latest-day Blocks / Fee override)

Output:
  output/csv/shutdown-price-all.csv  (columns: date, btc, energyEfficiencyRatio, feeInRewardPct)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
CSV_DIR = HERE / "output" / "csv"
JSON_DIR = HERE / "output" / "json"

DEFAULT_HASHRATE_CSV = CSV_DIR / "bitinfocharts_btc_hashrate_hs.csv"
FALLBACK_HASHRATE_CSV = CSV_DIR / "bitinfocharts_hashrate_history.csv"
DEFAULT_FEE_CSV = CSV_DIR / "bitinfocharts_btc_fee_in_reward_pct.csv"
DEFAULT_BLOCKS_CSV = CSV_DIR / "bitinfocharts_btc_blocks_per_day.csv"
DEFAULT_MAIN_SNAPSHOT_JSON = JSON_DIR / "bitinfocharts_btc_main_snapshot.json"
DEFAULT_OUT_CSV = CSV_DIR / "shutdown-price-all.csv"

MACHINE_HASHRATE_HS = 1e12
DEFAULT_ELECTRICITY_USD_PER_KWH = 0.05
DEFAULT_START_DATE = "2016-07-10"

# Real Bitcoin halving dates -> block subsidy (BTC)
HALVING_REWARDS: tuple[tuple[str, float], ...] = (
    ("2012-11-28", 25.0),
    ("2016-07-09", 12.5),
    ("2020-05-11", 6.25),
    ("2024-04-19", 3.125),
    ("2028-04-17", 1.5625),
)

# Power W per 1 TH/s reference machine; linear within each era
EFFICIENCY_EPOCHS: tuple[tuple[str, str, float, float], ...] = (
    ("2016-07-10", "2020-05-10", 100.0, 57.0),
    ("2020-05-11", "2024-04-18", 34.21, 29.55),
    ("2024-04-19", "2028-04-16", 17.5, 9.5),
)


@dataclass(frozen=True)
class DayInputs:
    network_hashrate_hs: float
    fee_in_reward_pct: float
    blocks_per_day: float


def parse_chart_date(text: str) -> dt.date:
    text = text.strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unsupported date: {text!r}")


def parse_date(text: str) -> dt.date:
    return parse_chart_date(text.replace("-", "/"))


def load_date_value_csv(path: Path, field: str | None = None) -> dict[dt.date, float]:
    if not path.is_file():
        return {}
    out: dict[dt.date, float] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return {}
        value_col = field or (reader.fieldnames[1] if len(reader.fieldnames) > 1 else reader.fieldnames[0])
        date_col = "date" if "date" in reader.fieldnames else reader.fieldnames[0]
        for row in reader:
            raw_date = (row.get(date_col) or "").strip()
            raw_value = (row.get(value_col) or "").strip()
            if not raw_date or not raw_value:
                continue
            try:
                day = parse_chart_date(raw_date.replace("-", "/"))
                out[day] = float(raw_value)
            except (ValueError, TypeError):
                continue
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hashrate-csv", default=str(DEFAULT_HASHRATE_CSV))
    parser.add_argument("--fallback-hashrate-csv", default=str(FALLBACK_HASHRATE_CSV))
    parser.add_argument("--fee-csv", default=str(DEFAULT_FEE_CSV))
    parser.add_argument("--blocks-csv", default=str(DEFAULT_BLOCKS_CSV))
    parser.add_argument("--main-snapshot-json", default=str(DEFAULT_MAIN_SNAPSHOT_JSON))
    parser.add_argument("--out", default=str(DEFAULT_OUT_CSV))
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--electricity-usd-per-kwh", type=float, default=DEFAULT_ELECTRICITY_USD_PER_KWH)
    return parser.parse_args()


def block_reward_btc(day: dt.date) -> float:
    reward = 50.0
    for date_text, next_reward in HALVING_REWARDS:
        if day >= parse_date(date_text):
            reward = next_reward
    return reward


def power_w_per_th(day: dt.date) -> float | None:
    for start_text, end_text, w_start, w_end in EFFICIENCY_EPOCHS:
        start = parse_date(start_text)
        end = parse_date(end_text)
        if day < start or day > end:
            continue
        span = (end - start).days
        if span <= 0:
            return w_start
        t = (day - start).days / span
        return w_start + t * (w_end - w_start)
    return None


def normalize_hashrate_hs(value: float) -> float:
    if value >= 1e14:
        return value
    return value * 1e12


def load_hashrate_hs(path: Path, fallback: Path) -> dict[dt.date, float]:
    for candidate in (path, fallback):
        if not candidate.is_file():
            continue
        if candidate.name.endswith("_hashrate_hs.csv") or "hashrate_hs" in candidate.name:
            data = load_date_value_csv(candidate, "hashrate_hs")
        else:
            data = load_date_value_csv(candidate, "btc")
        if data:
            return {day: normalize_hashrate_hs(v) for day, v in data.items()}
    raise SystemExit(
        f"hashrate csv not found: {path} (fallback {fallback}); run 2.download_hashrate.py first"
    )


def load_main_snapshot(path: Path) -> dict[str, float]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, float] = {}
    if isinstance(payload.get("blocks_last_24h"), (int, float)):
        out["blocks_last_24h"] = float(payload["blocks_last_24h"])
    if isinstance(payload.get("fee_in_reward_pct"), (int, float)):
        out["fee_in_reward_pct"] = float(payload["fee_in_reward_pct"])
    return out


def lookup_series(series: dict[dt.date, float], day: dt.date) -> float | None:
    if day in series:
        return series[day]
    return None


def build_day_inputs(
    day: dt.date,
    *,
    hashrate: dict[dt.date, float],
    fee_pct: dict[dt.date, float],
    blocks: dict[dt.date, float],
    main_snapshot: dict[str, float],
    latest_hashrate_day: dt.date | None,
) -> DayInputs | None:
    network_hs = lookup_series(hashrate, day)
    if network_hs is None or network_hs <= 0:
        return None

    fee = lookup_series(fee_pct, day)
    block_count = lookup_series(blocks, day)
    if latest_hashrate_day is not None and day == latest_hashrate_day:
        if "fee_in_reward_pct" in main_snapshot:
            fee = main_snapshot["fee_in_reward_pct"]
        if "blocks_last_24h" in main_snapshot:
            block_count = main_snapshot["blocks_last_24h"]

    if fee is None or block_count is None or block_count <= 0:
        return None
    if fee < 0 or fee >= 100:
        return None
    return DayInputs(network_hashrate_hs=network_hs, fee_in_reward_pct=fee, blocks_per_day=block_count)


def compute_shutdown_price(
    *,
    inputs: DayInputs,
    power_w: float,
    block_reward: float,
    electricity_usd_per_kwh: float,
) -> float | None:
    if power_w <= 0 or block_reward <= 0:
        return None
    fee_ratio = inputs.fee_in_reward_pct / 100.0
    if fee_ratio >= 1.0:
        return None
    reward_with_fees = block_reward / (1.0 - fee_ratio)
    network_daily_coins = inputs.blocks_per_day * reward_with_fees
    daily_electricity_usd = power_w / 1000.0 * electricity_usd_per_kwh * 24.0
    daily_btc_income = (MACHINE_HASHRATE_HS / inputs.network_hashrate_hs) * network_daily_coins
    if daily_btc_income <= 0:
        return None
    return daily_electricity_usd / daily_btc_income


@dataclass(frozen=True)
class ShutdownRow:
    btc: float
    energy_efficiency_ratio: float | None = None
    fee_in_reward_pct: float | None = None


def _parse_optional_positive_float(row: dict[str, str], *keys: str) -> float | None:
    for key in keys:
        raw = (row.get(key) or "").strip()
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        if value >= 0:
            return value
    return None


def _parse_energy_efficiency_ratio(row: dict[str, str]) -> float | None:
    value = _parse_optional_positive_float(row, "energyEfficiencyRatio", "energy_efficiency_ratio")
    return value if value is not None and value > 0 else None


def load_existing_shutdown_csv(path: Path) -> dict[dt.date, ShutdownRow]:
    if not path.is_file():
        return {}
    out: dict[dt.date, ShutdownRow] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_date = (row.get("date") or "").strip()
            raw_btc = (row.get("btc") or "").strip()
            if not raw_date or not raw_btc:
                continue
            try:
                btc = float(raw_btc)
                if btc <= 0:
                    continue
                fee = _parse_optional_positive_float(row, "feeInRewardPct", "fee_in_reward_pct")
                if fee is not None and fee >= 100:
                    fee = None
                out[parse_date(raw_date)] = ShutdownRow(
                    btc=btc,
                    energy_efficiency_ratio=_parse_energy_efficiency_ratio(row),
                    fee_in_reward_pct=fee,
                )
            except ValueError:
                continue
    return out


def merge_shutdown_rows(
    computed: dict[dt.date, ShutdownRow | None],
    existing: dict[dt.date, ShutdownRow],
) -> list[tuple[dt.date, ShutdownRow]]:
    all_days = sorted(set(computed) | set(existing))
    merged: list[tuple[dt.date, ShutdownRow]] = []
    for day in all_days:
        new_row = computed.get(day)
        old_row = existing.get(day)
        if new_row is not None and new_row.btc > 0:
            merged.append((day, new_row))
        elif old_row is not None and old_row.btc > 0:
            merged.append((day, old_row))
    return merged


def write_shutdown_csv(path: Path, rows: list[tuple[dt.date, ShutdownRow]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "btc", "energyEfficiencyRatio", "feeInRewardPct"])
        for day, row in rows:
            ratio_text = (
                row.energy_efficiency_ratio
                if row.energy_efficiency_ratio is not None
                else ""
            )
            fee_text = row.fee_in_reward_pct if row.fee_in_reward_pct is not None else ""
            writer.writerow([day.isoformat(), row.btc, ratio_text, fee_text])


def main() -> None:
    args = parse_args()
    start_date = parse_date(args.start_date)
    out_path = Path(args.out).resolve()
    existing = load_existing_shutdown_csv(out_path)

    hashrate = load_hashrate_hs(Path(args.hashrate_csv), Path(args.fallback_hashrate_csv))
    fee_pct = load_date_value_csv(Path(args.fee_csv), "fee_in_reward_pct")
    blocks = load_date_value_csv(Path(args.blocks_csv), "blocks_per_day")
    main_snapshot = load_main_snapshot(Path(args.main_snapshot_json))
    if not fee_pct or not blocks:
        raise SystemExit("fee/blocks csv missing; run 2.download_hashrate.py (bitinfocharts) first")

    latest_hashrate_day = max(hashrate.keys()) if hashrate else None
    computed: dict[dt.date, ShutdownRow | None] = {}

    for day in sorted(hashrate.keys()):
        if day < start_date:
            continue
        power_w = power_w_per_th(day)
        if power_w is None:
            computed[day] = None
            continue
        inputs = build_day_inputs(
            day,
            hashrate=hashrate,
            fee_pct=fee_pct,
            blocks=blocks,
            main_snapshot=main_snapshot,
            latest_hashrate_day=latest_hashrate_day,
        )
        if inputs is None:
            computed[day] = None
            continue
        shutdown_btc = compute_shutdown_price(
            inputs=inputs,
            power_w=power_w,
            block_reward=block_reward_btc(day),
            electricity_usd_per_kwh=float(args.electricity_usd_per_kwh),
        )
        if shutdown_btc is None or shutdown_btc <= 0:
            computed[day] = None
            continue
        computed[day] = ShutdownRow(
            btc=shutdown_btc,
            energy_efficiency_ratio=power_w,
            fee_in_reward_pct=inputs.fee_in_reward_pct,
        )

    merged = merge_shutdown_rows(computed, existing)
    if not merged:
        raise SystemExit("no shutdown rows after merge")

    write_shutdown_csv(out_path, merged)
    new_count = sum(1 for row in computed.values() if row is not None and row.btc > 0)
    kept = len(merged) - new_count
    print(
        f"DONE. wrote {len(merged):,} rows -> {out_path} "
        f"(computed={new_count}, kept_from_old~={max(kept, 0)}, {merged[0][0]} .. {merged[-1][0]})"
    )


if __name__ == "__main__":
    main()
