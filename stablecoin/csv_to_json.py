"""Convert stablecoin wide CSV into dashboard JSON.

Input:
  output/csv/stablecoins_marketcap.csv

Output:
  output/json/stablecoins_marketcap.json
    aggregate columns only: all_* and *_stablecoins
  output/json/stablecoins_marketcap_breakdown.json
    per-chain single-coin columns ({chain}_{coin})
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DATA_DOWNLOAD_ROOT = HERE.parent
if str(DATA_DOWNLOAD_ROOT) not in sys.path:
    sys.path.insert(0, str(DATA_DOWNLOAD_ROOT))
import _json_merge as JM  # noqa: E402

CSV_PATH = HERE / "output" / "csv" / "stablecoins_marketcap.csv"
JSON_PATH = HERE / "output" / "json" / "stablecoins_marketcap.json"
BREAKDOWN_JSON_PATH = HERE / "output" / "json" / "stablecoins_marketcap_breakdown.json"


def is_aggregate_column(key: str) -> bool:
    """all_* totals and per-chain *_stablecoins. Cross columns stay out of this file."""
    return key.startswith("all_") or key.endswith("_stablecoins")


def project_rows(rows: list[dict[str, Any]], *, aggregate: bool) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {"time": row["time"]}
        for key, value in row.items():
            if key == "time":
                continue
            if is_aggregate_column(key) == aggregate:
                item[key] = value
        if any(key != "time" for key in item):
            projected.append(item)
    return projected


def normalize_time_day(raw: str) -> str | None:
    """Normalize YYYY/M/D or YYYY-MM-DD to YYYY-MM-DD (dashboard + merge key)."""
    text = (raw or "").strip()
    if not text:
        return None
    slash = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})", text)
    if slash:
        y, m, d = slash.groups()
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return text[:10]
    return None


def normalize_rows(rows: list[Any]) -> list[dict[str, Any]]:
    """Normalize time keys and dedupe rows that differ only by date format."""
    by_day: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = normalize_time_day(str(row.get("time") or ""))
        if not day:
            continue
        item = dict(row)
        item["time"] = day
        if day not in by_day:
            by_day[day] = item
        else:
            by_day[day] = JM.merge_dict_fields(item, by_day[day])
    return [by_day[k] for k in sorted(by_day.keys())]


def load_csv_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handler:
        reader = csv.DictReader(handler)
        for row in reader:
            day = normalize_time_day((row.get("time") or "").strip())
            if not day:
                continue
            item: dict[str, Any] = {"time": day}
            for key, value in row.items():
                if key == "time" or value is None or str(value).strip() == "":
                    continue
                try:
                    item[key] = float(value)
                except ValueError:
                    item[key] = value
            rows.append(item)
    rows.sort(key=lambda item: str(item.get("time", "")))
    return rows


def write_json(path: Path, payload: dict[str, Any], *, compact: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv-in", default=str(CSV_PATH))
    parser.add_argument("--json-out", default=str(JSON_PATH))
    parser.add_argument("--breakdown-out", default=str(BREAKDOWN_JSON_PATH))
    parser.add_argument("--compact", action="store_true")
    return parser.parse_args()


def merge_payload(
    json_out: Path,
    incoming_rows: list[dict[str, Any]],
    *,
    aggregate: bool,
) -> dict[str, Any]:
    incoming_payload = {"code": "0", "msg": "success", "data": incoming_rows}
    existing = JM.load_json(json_out)
    if existing and isinstance(existing, dict):
        existing_rows = project_rows(normalize_rows(existing.get("data") or []), aggregate=aggregate)
        merged_data = JM.merge_list_of_dicts(existing_rows, incoming_rows, key_field="time")
        return {**existing, **incoming_payload, "data": merged_data}
    return incoming_payload


def write_split(
    json_out: Path,
    rows: list[dict[str, Any]],
    *,
    aggregate: bool,
    compact: bool,
) -> None:
    payload = merge_payload(json_out, rows, aggregate=aggregate)
    write_json(json_out, payload, compact=compact)
    columns = {key for row in payload["data"] for key in row if key != "time"}
    print(f"wrote {json_out} rows={len(payload['data'])} cols={len(columns)}")


def main() -> None:
    args = parse_args()
    csv_in = Path(args.csv_in).resolve()
    json_out = Path(args.json_out).resolve()
    breakdown_out = Path(args.breakdown_out).resolve()
    if not csv_in.is_file():
        raise SystemExit(f"CSV not found: {csv_in}")

    incoming_rows = normalize_rows(load_csv_rows(csv_in))
    write_split(
        json_out,
        project_rows(incoming_rows, aggregate=True),
        aggregate=True,
        compact=args.compact,
    )
    write_split(
        breakdown_out,
        project_rows(incoming_rows, aggregate=False),
        aggregate=False,
        compact=args.compact,
    )


if __name__ == "__main__":
    main()
