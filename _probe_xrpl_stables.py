#!/usr/bin/env python3
import json, urllib.request
UA = {"User-Agent": "dashboard-data/probe"}
req = urllib.request.Request(
    "https://stablecoins.llama.fi/stablecoins?includePrices=true", headers=UA
)
with urllib.request.urlopen(req, timeout=60) as r:
    doc = json.loads(r.read())
rows = []
for a in doc.get("peggedAssets") or []:
    circ = a.get("chainCirculating") or {}
    xrpl = circ.get("XRPL") or circ.get("xrpl")
    if not xrpl:
        continue
    cur = xrpl.get("current") or {}
    usd = cur.get("peggedUSD")
    if not isinstance(usd, (int, float)) or usd <= 0:
        continue
    rows.append((float(usd), str(a.get("symbol") or ""), str(a.get("name") or ""), a.get("id")))
rows.sort(reverse=True)
total = sum(x[0] for x in rows)
print("XRPL assets", len(rows), "sum", round(total, 2))
for usd, sym, name, aid in rows[:12]:
    print(f"  {sym:12} {name[:42]:42} {usd:,.0f} id={aid}")
req2 = urllib.request.Request("https://stablecoins.llama.fi/stablecoincharts/XRPL", headers=UA)
with urllib.request.urlopen(req2, timeout=40) as r:
    series = json.loads(r.read())
print("charts n", len(series) if isinstance(series, list) else type(series))
if isinstance(series, list) and series:
    last = series[-1]
    print("last", last)
    # find first day >= 1e9
    for row in series:
        tot = (row.get("totalCirculatingUSD") or {}).get("peggedUSD")
        if isinstance(tot, (int, float)) and tot >= 1e9:
            print("first >=1B", row.get("date"), tot)
            break
