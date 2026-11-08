#!/usr/bin/env python3
"""Compute Robinhood Chain stock AUM 30d delta from cached rwa_aum.json (free/local)."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

p = Path("/root/dashboard/public/json/robinhood-chain/rwa_aum.json")
doc = json.loads(p.read_text(encoding="utf-8"))
rows = doc["data"]

by_date: dict[str, dict[str, float]] = {}
for r in rows:
    d = r["block_date"]
    by_date.setdefault(d, {})
    by_date[d][r["asset_class"]] = float(r["aum_usd"])

dates = sorted(by_date)
print("date range", dates[0], "->", dates[-1], "n=", len(dates))
print("updated_at", doc.get("updated_at"), "last block", dates[-1])

end = dates[-1]
end_d = datetime.strptime(end, "%Y-%m-%d").date()
start_target = (end_d - timedelta(days=30)).isoformat()
# nearest <= start_target
start = max((d for d in dates if d <= start_target), default=dates[0])
print("window", start, "->", end)

for label, d in [("start", start), ("end", end)]:
    print(label, d, by_date[d])

classes = sorted({c for day in (start, end) for c in by_date[day]})
print("\n=== 30d delta by asset_class (RH only) ===")
total0 = total1 = 0.0
for c in classes:
    a = by_date[start].get(c, 0.0)
    b = by_date[end].get(c, 0.0)
    total0 += a
    total1 += b
    print(f"{c}: {a/1e6:.3f}M -> {b/1e6:.3f}M  delta={(b-a)/1e6:+.3f}M")
print(f"ALL: {total0/1e6:.3f}M -> {total1/1e6:.3f}M  delta={(total1-total0)/1e6:+.3f}M")
stock_d = by_date[end].get("stock", 0) - by_date[start].get("stock", 0)
print(f"\nTT tweet RH stock-ish target ~+$129.3M; our stock-only delta ${(stock_d)/1e6:+.2f}M")
print("NOTE: Dune series stalled at", end, "; TT tweet window may differ.")
