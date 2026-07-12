"""Step 4/4 — Convert mining shutdown CSV files into dashboard JSON payloads.

Requires prior steps (or `python run_all.py`).

Inputs:
  - output/csv/shutdown-price-all.csv
  - output/csv/realtime_mining_shutdown_price_snapshot.csv

Outputs:
  - output/json/mining_shutdown_price.json
  - output/json/realtime_mining_shutdown_price_snapshot.json
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DATA_DOWNLOAD_ROOT = HERE.parent
if str(DATA_DOWNLOAD_ROOT) not in sys.path:
    sys.path.insert(0, str(DATA_DOWNLOAD_ROOT))
import _json_merge as JM  # noqa: E402
HISTORY_CSV_PATH = HERE / "output" / "csv" / "shutdown-price-all.csv"
HISTORY_JSON_PATH = HERE / "output" / "json" / "mining_shutdown_price.json"
REALTIME_CSV_PATH = HERE / "output" / "csv" / "realtime_mining_shutdown_price_snapshot.csv"
REALTIME_JSON_PATH = HERE / "output" / "json" / "realtime_mining_shutdown_price_snapshot.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-in", default=str(HISTORY_CSV_PATH), help="History input CSV path")
    parser.add_argument("--history-out", default=str(HISTORY_JSON_PATH), help="History output JSON path")
    parser.add_argument("--realtime-in", default=str(REALTIME_CSV_PATH), help="Realtime input CSV path")
    parser.add_argument("--realtime-out", default=str(REALTIME_JSON_PATH), help="Realtime output JSON path")
    parser.add_argument("--skip-history", action="store_true", help="Skip history CSV -> JSON conversion")
    parser.add_argument("--skip-realtime", action="store_true", help="Skip realtime CSV -> JSON conversion")
    parser.add_argument("--compact", action="store_true", help="Write compact JSON")
    return parser.parse_args()


def normalize_date(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return raw
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def maybe_float(value: str) -> float | None:
    s = (value or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def resolve_columns(fieldnames: list[str]) -> tuple[str, str]:
    if not fieldnames:
        raise SystemExit("CSV has no header.")
    date_col = fieldnames[0]
    value_col = fieldnames[1] if len(fieldnames) > 1 else fieldnames[0]
    return date_col, value_col


def load_csv_rows_with_fallback(csv_path: Path) -> list[dict[str, str]]:
    encodings = ("utf-8-sig", "utf-8", "gbk", "gb18030")
    for encoding in encodings:
        try:
            with csv_path.open("r", encoding=encoding, newline="") as handler:
                reader = csv.DictReader(handler)
                return list(reader)
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"Unable to decode CSV with supported encodings: {csv_path}")


def build_payload(rows: list[dict[str, str]]) -> dict[str, Any]:
    fieldnames = list(rows[0].keys()) if rows else []
    date_col, value_col = resolve_columns(fieldnames)

    data: list[dict[str, Any]] = []
    for row in rows:
        date_text = normalize_date(str(row.get(date_col, "")))
        price_value = maybe_float(str(row.get(value_col, "")))
        if not date_text or price_value is None:
            continue
        item: dict[str, Any] = {
            "date": date_text,
            "btc": price_value,
        }
        energy_ratio = maybe_float(
            str(row.get("energyEfficiencyRatio") or row.get("energy_efficiency_ratio") or "")
        )
        if energy_ratio is not None and energy_ratio > 0:
            item["energyEfficiencyRatio"] = energy_ratio
        fee_pct = maybe_float(str(row.get("feeInRewardPct") or row.get("fee_in_reward_pct") or ""))
        if fee_pct is not None and fee_pct >= 0 and fee_pct < 100:
            item["feeInRewardPct"] = fee_pct
        data.append(item)
    return {"code": "0", "msg": "success", "data": data}


def maybe_number(value: str) -> float | int | None:
    s = (value or "").strip()
    if not s:
        return None
    try:
        n = float(s)
    except ValueError:
        return None
    if n.is_integer():
        return int(n)
    return n


def build_realtime_payload(rows: list[dict[str, str]]) -> dict[str, Any]:
    data: list[dict[str, Any]] = []
    for row in rows:
        coin = str(row.get("coin", "")).strip()
        miner_model = str(row.get("minerModel", "")).strip()
        if not coin or not miner_model:
            continue

        item = {
            "coin": coin,
            "minerModel": miner_model,
            "electricityPriceCents": maybe_number(str(row.get("electricityPriceCents", ""))),
            "singlePowerConsumptionW": maybe_number(str(row.get("singlePowerConsumptionW", ""))),
            "singleHashRate": maybe_number(str(row.get("singleHashRate", ""))),
            "singleHashRateUnit": str(row.get("singleHashRateUnit", "")).strip() or None,
            "energyEfficiencyRatio": maybe_number(str(row.get("energyEfficiencyRatio", ""))),
            "machinePriceUsd": maybe_number(str(row.get("machinePriceUsd", ""))),
            "otherCostUsdPerDay": maybe_number(str(row.get("otherCostUsdPerDay", ""))),
            "electricityCostUsdPerDay": maybe_number(str(row.get("electricityCostUsdPerDay", ""))),
            "electricityShutdownPriceUsd": maybe_number(str(row.get("electricityShutdownPriceUsd", ""))),
            "shutdownPriceUsd": maybe_number(str(row.get("shutdownPriceUsd", ""))),
            "paybackDays": maybe_number(str(row.get("paybackDays", ""))),
            "roiPercent": maybe_number(str(row.get("roiPercent", ""))),
            "dailyTheoreticalProfitUsd": maybe_number(str(row.get("dailyTheoreticalProfitUsd", ""))),
            "dailyNetProfitUsd": maybe_number(str(row.get("dailyNetProfitUsd", ""))),
            "priceSource": str(row.get("priceSource", "")).strip() or None,
            "fetchedAt": str(row.get("fetchedAt", "")).strip() or None,
        }
        data.append(item)
    return {
        "code": 0,
        "msg": "success",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "data": data,
    }


def write_json(path: Path, payload: dict[str, Any], compact: bool, *, merge_fn) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = JM.load_json(path)
    merged = JM.merge_wrapped_data(existing, payload, merge_fn)
    if compact:
        path.write_text(json.dumps(merged, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    else:
        path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    args = parse_args()
    if not args.skip_history:
        history_in = Path(args.history_in).resolve()
        history_out = Path(args.history_out).resolve()
        if not history_in.exists():
            raise SystemExit(f"History CSV not found: {history_in}")
        history_rows = load_csv_rows_with_fallback(history_in)
        history_payload = build_payload(history_rows)
        write_json(
            history_out,
            history_payload,
            args.compact,
            merge_fn=lambda ex, inc: JM.merge_list_of_dicts(ex, inc, key_field="date"),
        )
        print(f"wrote history {history_out} rows={len(history_payload['data'])}")

    if not args.skip_realtime:
        realtime_in = Path(args.realtime_in).resolve()
        realtime_out = Path(args.realtime_out).resolve()
        if not realtime_in.exists():
            raise SystemExit(f"Realtime CSV not found: {realtime_in}")
        realtime_rows = load_csv_rows_with_fallback(realtime_in)
        realtime_payload = build_realtime_payload(realtime_rows)
        write_json(
            realtime_out,
            realtime_payload,
            args.compact,
            merge_fn=lambda ex, inc: JM.merge_list_of_dicts(
                ex, inc, key_fields=("coin", "minerModel", "electricityPriceCents")
            ),
        )
        print(f"wrote realtime {realtime_out} rows={len(realtime_payload['data'])}")


if __name__ == "__main__":
    main()

