import json
import urllib.request
from datetime import datetime, timezone

UA = {"User-Agent": "dashboard-probe"}
CANDS = {
    "MET": ["meteora", "meteora-dlmm", "meteora-amm"],
    "PUMP": ["pump", "pump-fun"],
    "GMX": ["gmx", "gmx-v2"],
    "RLB": ["rollbit", "rollbit-fun"],
    "MPLX": ["metaplex"],
    "HYPE": ["hyperliquid", "hyperliquid-perps"],
    "LIT": ["lighter", "lit-protocol", "litentry"],
    "AAVE": ["aave", "aave-v3"],
}


def get(url: str):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.status, json.loads(resp.read().decode())
    except Exception as exc:
        return type(exc).__name__, str(exc)[:160]


for sym, slugs in CANDS.items():
    for slug in slugs:
        url = f"https://api.llama.fi/summary/fees/{slug}?dataType=dailyHoldersRevenue"
        code, doc = get(url)
        if not isinstance(doc, dict):
            print(sym, slug, code, doc)
            continue
        chart = doc.get("totalDataChart") or []
        days = []
        total = 0.0
        last = None
        for pt in chart:
            if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                continue
            day = datetime.fromtimestamp(int(pt[0]), tz=timezone.utc).strftime("%Y-%m-%d")
            val = float(pt[1] or 0)
            last = (day, val)
            if "2026-01-01" <= day < "2026-10-21":
                total += val
                days.append(day)
        name = doc.get("displayName") or doc.get("name")
        print(
            f"{sym} {slug} n={len(chart)} ytd_m={total/1e6:.2f} "
            f"ndays={len(days)} first={days[0] if days else None} last={last} name={name}"
        )
