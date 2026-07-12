"""output/holdings.csv — 1.download 写入，2.mark_holdings 标红，3.csv_to_json 读取。"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
HOLDINGS_CSV = HERE / "output" / "holdings.csv"

FIELDS = [
    "coin_id",
    "type",
    "entity_id",
    "name",
    "symbol",
    "country",
    "total_holdings",
    "total_entry_value_usd",
    "total_current_value_usd",
    "percentage_of_total_supply",
    "skip_json",
]


def row_from_overview(coin_id: str, entity_type: str, row: Dict[str, Any]) -> Dict[str, str]:
    return {
        "coin_id": coin_id,
        "type": entity_type,
        "entity_id": str(row.get("entity_id") or ""),
        "name": str(row.get("name") or ""),
        "symbol": str(row.get("symbol") or ""),
        "country": str(row.get("country") or ""),
        "total_holdings": str(row.get("total_holdings") or ""),
        "total_entry_value_usd": str(row.get("total_entry_value_usd") or ""),
        "total_current_value_usd": str(row.get("total_current_value_usd") or ""),
        "percentage_of_total_supply": str(row.get("percentage_of_total_supply") or ""),
        "skip_json": "0",
    }


def row_key(row: Dict[str, str]) -> tuple[str, ...]:
    coin_id = str(row.get("coin_id") or "").strip()
    entity_type = str(row.get("type") or "").strip()
    entity_id = str(row.get("entity_id") or "").strip()
    if entity_id:
        return (coin_id, entity_type, entity_id)
    name = str(row.get("name") or "").strip()
    country = str(row.get("country") or "").strip()
    return (coin_id, entity_type, name, country)


def normalize_row(row: Dict[str, str]) -> Dict[str, str]:
    return {k: str(row.get(k) or "") for k in FIELDS}


def read_rows() -> List[Dict[str, str]]:
    if not HOLDINGS_CSV.exists():
        return []
    with HOLDINGS_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        return [normalize_row(r) for r in csv.DictReader(f)]


def merge_rows(existing: List[Dict[str, str]], incoming: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """本轮有数据的币种整币更新；未出现在 incoming 的币种沿用旧行。skip_json 沿用旧值。"""
    coins_updated = {str(r.get("coin_id") or "").strip() for r in incoming if str(r.get("coin_id") or "").strip()}
    merged: Dict[tuple[str, ...], Dict[str, str]] = {
        row_key(r): normalize_row(r)
        for r in existing
        if str(r.get("coin_id") or "").strip() not in coins_updated
    }
    for raw in incoming:
        row = normalize_row(raw)
        key = row_key(row)
        old = merged.get(key)
        if old:
            row["skip_json"] = old.get("skip_json") or row.get("skip_json") or "0"
        merged[key] = row
    return sorted(merged.values(), key=lambda r: (r["coin_id"], r["type"], r["name"], r["entity_id"]))


def write_rows(rows: List[Dict[str, str]]) -> None:
    HOLDINGS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with HOLDINGS_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(normalize_row(row))


def write_rows_merged(incoming: List[Dict[str, str]]) -> tuple[int, int]:
    """合并写入：返回 (合并后总行数, 本轮更新行数)。"""
    existing = read_rows()
    merged = merge_rows(existing, incoming)
    write_rows(merged)
    return len(merged), len(incoming)
