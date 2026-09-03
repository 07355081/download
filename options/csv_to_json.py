"""Convert DVOL CSV files into dashboard JSON files.

Input:
  output/csv/dvol_<SYMBOL>_<START>_<END>_<RESOLUTION>.csv

Output:
  output/json/dvol_<SYMBOL>_<START>_<END>_<RESOLUTION>.json
  output/json/dvol_<SYMBOL>_latest_<RESOLUTION>.json  (dashboard / copy_to_dashboard)
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
CSV_DIR = HERE / "output" / "csv"
JSON_DIR = HERE / "output" / "json"

NAME_RE = re.compile(r"^dvol_(?P<symbol>[A-Z0-9]+)_(?P<start>\d{8})_(?P<end>\d{8})_(?P<resolution>[A-Z0-9]+)\.csv$")


def to_num(v: str) -> float | str:
    s = (v or "").strip()
    if s == "":
        return s
    try:
        return float(s)
    except ValueError:
        return s


def latest_json_path(symbol: str, resolution: str) -> Path:
    return JSON_DIR / f"dvol_{symbol}_latest_{resolution}.json"


def merge_latest_meta(
    existing_meta: dict[str, Any] | None,
    incoming_meta: dict[str, Any],
) -> dict[str, Any]:
    existing_meta = existing_meta or {}
    start = incoming_meta.get("start") or existing_meta.get("start")
    end = incoming_meta.get("end") or existing_meta.get("end")
    if existing_meta.get("start") and incoming_meta.get("start"):
        start = min(str(existing_meta["start"]), str(incoming_meta["start"]))
    if existing_meta.get("end") and incoming_meta.get("end"):
        end = max(str(existing_meta["end"]), str(incoming_meta["end"]))
    return {
        "currency": incoming_meta.get("currency") or existing_meta.get("currency"),
        "resolution": incoming_meta.get("resolution") or existing_meta.get("resolution"),
        "start": start,
        "end": end,
    }


def write_json_payload(path: Path, payload: dict[str, Any], *, compact: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_latest_json(
    symbol: str,
    resolution: str,
    meta: dict[str, Any],
    data_rows: list[list[Any]],
    *,
    compact: bool,
) -> None:
    latest_path = latest_json_path(symbol, resolution)
    incoming = {"meta": meta, "data": data_rows}
    existing = JM.load_json(latest_path)
    if existing and isinstance(existing, dict):
        merged_data = JM.merge_dvol_rows(existing.get("data"), data_rows)
        merged_meta = merge_latest_meta(
            existing.get("meta") if isinstance(existing.get("meta"), dict) else None,
            meta,
        )
        payload = {"meta": merged_meta, "data": merged_data}
    else:
        payload = incoming
    write_json_payload(latest_path, payload, compact=compact)


def prune_stale_dated_json(symbol: str, resolution: str, keep_stem: str) -> None:
    prefix = f"dvol_{symbol}_"
    suffix = f"_{resolution}.json"
    for path in JSON_DIR.glob(f"{prefix}*{suffix}"):
        if "_latest_" in path.name:
            continue
        if path.stem == keep_stem:
            continue
        path.unlink()
        print(f"  removed stale {path.name}")


def convert_one(csv_path: Path, json_path: Path, *, compact: bool) -> int:
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    match = NAME_RE.match(csv_path.name)
    if not match:
        raise ValueError(f"Unexpected file name: {csv_path.name}")
    symbol = match.group("symbol")
    resolution = match.group("resolution")
    meta = {
        "currency": symbol,
        "resolution": resolution,
        "start": match.group("start"),
        "end": match.group("end"),
    }
    data_rows: list[list[Any]] = []
    for row in rows:
        data_rows.append(
            [
                (row.get("date") or "").strip(),
                to_num(row.get("open") or ""),
                to_num(row.get("high") or ""),
                to_num(row.get("low") or ""),
                to_num(row.get("close") or ""),
            ]
        )

    payload = {"meta": meta, "data": data_rows}
    existing = JM.load_json(json_path)
    if existing and isinstance(existing, dict):
        merged_data = JM.merge_dvol_rows(existing.get("data"), data_rows)
        payload = {**existing, **payload, "data": merged_data}
    write_json_payload(json_path, payload, compact=compact)
    write_latest_json(symbol, resolution, meta, payload["data"], compact=compact)
    return len(payload["data"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default=None, help="Only convert one symbol, e.g. BTC")
    parser.add_argument("--compact", action="store_true", help="Write compact JSON (no indentation)")
    args = parser.parse_args()

    if not CSV_DIR.exists():
        raise SystemExit(f"CSV dir not found: {CSV_DIR}")
    JSON_DIR.mkdir(parents=True, exist_ok=True)

    files = sorted(CSV_DIR.glob("dvol_*.csv"))
    if args.symbol:
        symbol = args.symbol.upper().strip()
        files = [path for path in files if path.name.startswith(f"dvol_{symbol}_")]
    if not files:
        raise SystemExit("No dvol CSV files found.")

    total = 0
    for i, csv_path in enumerate(files, 1):
        match = NAME_RE.match(csv_path.name)
        if not match:
            raise ValueError(f"Unexpected file name: {csv_path.name}")
        json_path = JSON_DIR / f"{csv_path.stem}.json"
        rows = convert_one(csv_path, json_path, compact=args.compact)
        total += rows
        prune_stale_dated_json(match.group("symbol"), match.group("resolution"), csv_path.stem)
        print(
            f"[{i}/{len(files)}] wrote {json_path.name} + "
            f"{latest_json_path(match.group('symbol'), match.group('resolution')).name} ({rows} rows)"
        )
    print(f"\nDONE. files={len(files)} rows={total} -> {JSON_DIR}")


if __name__ == "__main__":
    main()
