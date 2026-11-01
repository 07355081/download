# -*- coding: utf-8 -*-
"""从已有 RootData 融资明细汇总年度额度档位份额（不新抓）。

输出：output/json/financing_deal_bucket.json
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "output" / "json" / "financing_deal_bucket.json"
SRC_CANDIDATES = [
    ROOT / "financing-news" / "output" / "json" / "fundraising_deals.json",
    Path("/root/dashboard/public/json/financing-news/fundraising_deals.json"),
]
MID_LO = 3_000_000.0
MID_HI = 10_000_000.0


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

    by_year: dict[int, Counter] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = str(row.get("date") or "")[:10]
        if len(day) < 4 or not day[:4].isdigit():
            continue
        year = int(day[:4])
        amt = row.get("amount")
        if not isinstance(amt, (int, float)) or float(amt) <= 0:
            continue
        a = float(amt)
        bucket = by_year.setdefault(year, Counter())
        if a < MID_LO:
            bucket["lt3"] += 1
        elif a <= MID_HI:
            bucket["mid"] += 1
        else:
            bucket["gt10"] += 1

    years_out: list[dict[str, Any]] = []
    for year in sorted(by_year):
        c = by_year[year]
        tot = c["lt3"] + c["mid"] + c["gt10"]
        if tot <= 0:
            continue
        years_out.append(
            {
                "year": year,
                "rounds_with_amount": tot,
                "lt3m": c["lt3"],
                "mid_3_10m": c["mid"],
                "gt10m": c["gt10"],
                "mid_share_pct": round(c["mid"] / tot * 100.0, 2),
                "gt10_share_pct": round(c["gt10"] / tot * 100.0, 2),
                "complete": year < date.today().year,
            }
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "source": "rootdata_fundraising_deals",
            "note": "300万–1000万美元档与>1000万档占有金额轮次份额。非 CryptoRank。",
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_file": str(src),
        },
        "data": years_out,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    latest = next((y for y in reversed(years_out) if y["complete"]), years_out[-1] if years_out else None)
    print(f"[financing-deal-bucket] years={len(years_out)} latest_complete={latest} → {OUT}")


if __name__ == "__main__":
    main()
