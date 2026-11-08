# -*- coding: utf-8 -*-
import json
from pathlib import Path

base = Path(__file__).resolve().parent / "output/json"
for name in (
    "cex_exchange_monthly_spot_volume_usd.json",
    "cex_exchange_monthly_futures_volume_usd.json",
):
    p = base / name
    wrapper = json.loads(p.read_text(encoding="utf-8"))
    data = wrapper["data"]
    rows = data["rows"]
    print("===", name, "row_count", data.get("row_count"), "n", len(rows), "ex", data.get("exchanges"))
    print("row0", rows[0] if rows else None)
    months = sorted({str(r.get("date", r.get("month", "")))[:7] for r in rows})
    print("months", months[-10:])
    for m in ("2026-09", "2026-10"):
        mr = [r for r in rows if str(r.get("date", r.get("month", ""))).startswith(m)]
        print(m, "n", len(mr))
        if mr:
            # sum numeric fields except date/exchange
            nums = []
            for r in mr:
                for k, v in r.items():
                    if k in ("date", "month", "exchange", "Exchange"):
                        continue
                    if isinstance(v, (int, float)):
                        nums.append((r.get("exchange") or r.get("Exchange") or k, v))
            print("  top", sorted(nums, key=lambda x: -x[1])[:5])
