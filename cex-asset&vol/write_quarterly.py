# -*- coding: utf-8 -*-
"""CEX 现货/合约月成交 → 完整季度合计，供 lark-feeds/cex-quarter-volume 画图。

只写入已结束且凑满三个月的季度。当季未收完的月份不进图。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _paths import OUTPUT_JSON_DIR

OUT_NAME = "cex_exchange_quarterly_futures_volume_usd.json"


def _load(path: Path) -> list[dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = doc.get("data", {}).get("rows") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError(f"{path.name} 没有 data.rows")
    return [row for row in rows if isinstance(row, dict)]


def _complete_quarters(rows: list[dict[str, Any]], today: str) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        month = row.get("month")
        total = row.get("ALL")
        if not isinstance(month, str) or len(month) < 7:
            continue
        if not isinstance(total, (int, float)) or not total > 0:
            continue
        year = int(month[0:4])
        mon = int(month[5:7])
        if mon < 1 or mon > 12:
            continue
        quarter = (mon - 1) // 3 + 1
        if quarter == 4:
            end_day = f"{year}-12-31"
        elif quarter == 3:
            end_day = f"{year}-09-30"
        elif quarter == 2:
            end_day = f"{year}-06-30"
        else:
            end_day = f"{year}-03-31"
        if end_day >= today:
            continue
        key = f"{year}Q{quarter}"
        item = buckets.get(key)
        if item is None:
            item = {"month": key, "end": end_day, "year": year, "quarter": quarter, "usd": 0.0, "months": set()}
            buckets[key] = item
        if month in item["months"]:
            continue
        item["months"].add(month)
        item["usd"] += float(total)
    points = [item for item in buckets.values() if len(item["months"]) >= 3]
    points.sort(key=lambda item: item["end"])
    out: list[dict[str, Any]] = []
    prev: dict[str, Any] | None = None
    for item in points:
        usd = round(item["usd"], 2)
        row: dict[str, Any] = {"month": item["month"], "usd": usd}
        if prev is not None:
            prev_year, prev_quarter = prev["year"], prev["quarter"]
            expect_year = item["year"] if item["quarter"] > 1 else item["year"] - 1
            expect_quarter = item["quarter"] - 1 if item["quarter"] > 1 else 4
            if prev_year == expect_year and prev_quarter == expect_quarter and prev["usd"] > 0:
                row["mom_pct"] = round((usd / prev["usd"] - 1) * 100, 2)
        prev = {"year": item["year"], "quarter": item["quarter"], "usd": usd}
        out.append(row)
    return out


def build_payload(spot_rows: list[dict[str, Any]], futures_rows: list[dict[str, Any]], today: str | None = None) -> dict[str, Any]:
    day = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    spot = _complete_quarters(spot_rows, day)
    futures = _complete_quarters(futures_rows, day)
    return {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Monthly CEX spot and futures totals, summed into closed quarters",
        "series": [
            {"label": "现货", "labelEn": "Spot", "months": spot},
            {"label": "合约", "labelEn": "Futures", "months": futures},
        ],
    }


def write_quarterly_file(
    spot_path: Path,
    futures_path: Path,
    out_path: Path | None = None,
) -> Path:
    payload = build_payload(_load(spot_path), _load(futures_path))
    dest = out_path or (OUTPUT_JSON_DIR / OUT_NAME)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return dest


def main() -> None:
    spot = OUTPUT_JSON_DIR / "cex_exchange_monthly_spot_volume_usd.json"
    futures = OUTPUT_JSON_DIR / "cex_exchange_monthly_futures_volume_usd.json"
    dest = write_quarterly_file(spot, futures)
    doc = json.loads(dest.read_text(encoding="utf-8"))
    for block in doc["series"]:
        months = block["months"]
        last = months[-1]["month"] if months else "none"
        print(f"{block['labelEn']}: {len(months)} quarters, latest {last}")
    print(dest)


if __name__ == "__main__":
    main()
