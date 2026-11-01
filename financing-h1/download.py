# -*- coding: utf-8 -*-
"""上半年融资轮次：总笔数 / 种子+Pre-Seed / A 轮及以后（RootData 明细）。

窗口：自然年 1 月 1 日–6 月 30 日。非 CryptoRank。
输出：output/json/financing_h1.json
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "output" / "json" / "financing_h1.json"
SRC_CANDIDATES = [
    ROOT / "financing-news" / "output" / "json" / "fundraising_deals.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "financing-news" / "fundraising_deals.json",
    Path("/root/dashboard/public/json/financing-news/fundraising_deals.json"),
]
SEED = {"Pre-Seed", "Seed", "Seed Plus"}
A_PLUS = {"Series A", "Series A1", "Series B", "Series B1", "Series C", "Series D", "Series E", "Series F"}


def resolve_src() -> Path:
    for path in SRC_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 fundraising_deals.json")


def main() -> None:
    src = resolve_src()
    doc = json.loads(src.read_text(encoding="utf-8"))
    rows = doc.get("data") if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        raise RuntimeError("invalid deals json")

    years: dict[int, dict[str, int]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = str(row.get("date") or "")[:10]
        if len(raw) < 10:
            continue
        try:
            day = date.fromisoformat(raw)
        except ValueError:
            continue
        if day.month > 6:
            continue
        slot = years.setdefault(day.year, {"total": 0, "seed": 0, "a_plus": 0})
        slot["total"] += 1
        rnd = str(row.get("round") or "").strip()
        if rnd in SEED:
            slot["seed"] += 1
        if rnd in A_PLUS:
            slot["a_plus"] += 1

    today = date.today()
    latest_complete = today.year - 1 if today.month <= 6 else today.year
    data = []
    for year in sorted(years):
        row = years[year]
        data.append(
            {
                "year": year,
                "start": f"{year}-01-01",
                "end": f"{year}-06-30",
                "total": row["total"],
                "seed": row["seed"],
                "a_plus": row["a_plus"],
                "complete": year <= latest_complete,
            }
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "code": "0",
        "msg": "success",
        "source": "RootData via financing-news/fundraising_deals.json",
        "note": "H1=1/1–6/30。种子=Pre-Seed/Seed/Seed Plus；A+=Series A 及以后。非 CryptoRank。",
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest_complete_year": latest_complete if any(r["year"] == latest_complete for r in data) else None,
        "data": data,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[financing-h1] years={len(data)} latest_complete={payload['latest_complete_year']} → {OUT}")


if __name__ == "__main__":
    main()
