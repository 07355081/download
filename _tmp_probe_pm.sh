#!/bin/bash
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=40 "$HOST" 'python3 - <<"PY"
import json,urllib.request
from datetime import datetime,timezone
from collections import defaultdict

def get(u):
  req=urllib.request.Request(u,headers={"User-Agent":"t"})
  with urllib.request.urlopen(req,timeout=60) as r:
    return json.loads(r.read().decode())

# fees overview - find kalshi / prediction
ov=get("https://api.llama.fi/overview/fees?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyFees")
protos=ov.get("protocols") or []
hits=[]
for p in protos:
  name=((p.get("name") or "")+" "+(p.get("displayName") or "")+" "+(p.get("module") or "")+" "+(p.get("category") or "")).lower()
  if any(k in name for k in ["kalshi","polymarket","prediction"]):
    hits.append({k:p.get(k) for k in ["name","displayName","module","category","total24h","total7d","total30d"]})
print("fee hits", hits)

# summary kalshi
for slug in ["kalshi","Kalshi","polymarket","polymarket-us","polymarket-international"]:
  try:
    d=get(f"https://api.llama.fi/summary/fees/{slug}?dataType=dailyFees")
    chart=d.get("totalDataChart") or []
    print("OK", slug, "n", len(chart), "24h", d.get("total24h"), "30d", d.get("total30d"), "name", d.get("name"), "cat", d.get("category"))
    # may 2026 sum
    s=0
    for ts,v in chart:
      day=datetime.fromtimestamp(int(ts),timezone.utc).strftime("%Y-%m")
      if day=="2026-05": s+=float(v or 0)
    print("  may2026", round(s/1e6,2), "M")
  except Exception as e:
    print("FAIL", slug, e)

# volumes from dashboard json if any
from pathlib import Path
for p in [
 Path("/root/dashboard/public/json/prediction-markets/prediction_markets_quarterly.json"),
 Path("/root/dashboard/public/json/prediction_markets_quarterly.json"),
 Path("/root/dashboard/public/json/pm-fees/pm_fees.json"),
]:
  if p.exists():
    d=json.loads(p.read_text())
    print("FILE", p, list(d)[:10] if isinstance(d,dict) else type(d))
    if isinstance(d,dict) and "months" in d:
      print(" months tail", d["months"][-3:])
    if isinstance(d,dict) and "latest" in d:
      print(" latest", str(d["latest"])[:200])
PY'
