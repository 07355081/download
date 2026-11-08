#!/usr/bin/env python3
"""CoinGecko free: tokenized-stock mcap by platform (approx)."""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from collections import defaultdict

UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def get_json(url: str):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


# 1) markets list
markets = []
for page in range(1, 6):
    url = (
        "https://api.coingecko.com/api/v3/coins/markets"
        f"?vs_currency=usd&category=tokenized-stock&order=market_cap_desc"
        f"&per_page=50&page={page}&sparkline=false"
    )
    try:
        batch = get_json(url)
    except Exception as e:
        print("markets page", page, "FAIL", e)
        break
    if not batch:
        break
    markets.extend(batch)
    print("page", page, "got", len(batch), "total", len(markets))
    time.sleep(1.2)

print("markets total", len(markets), "mcap_sum", sum((m.get("market_cap") or 0) for m in markets))

# 2) platforms map
print("fetch coins/list include_platform")
try:
    clist = get_json("https://api.coingecko.com/api/v3/coins/list?include_platform=true")
    print("list n", len(clist))
except Exception as e:
    print("list FAIL", e)
    clist = []

by_id = {c["id"]: c for c in clist if isinstance(c, dict) and c.get("id")}
chain_mcap = defaultdict(float)
unknown = 0
multi = 0
for m in markets:
    cid = m.get("id")
    mcap = float(m.get("market_cap") or 0)
    if mcap <= 0:
        continue
    platforms = (by_id.get(cid) or {}).get("platforms") or {}
    # platforms: {ethereum: 0x..., binance-smart-chain: ..., ...}
    keys = [k for k, v in platforms.items() if v]
    if not keys:
        unknown += 1
        chain_mcap["unknown/native"] += mcap
        continue
    if len(keys) > 1:
        multi += 1
        # attribute full mcap to each? better split equally for rough view
        share = mcap / len(keys)
        for k in keys:
            chain_mcap[k] += share
    else:
        chain_mcap[keys[0]] += mcap

print("unknown", unknown, "multi", multi)
ranked = sorted(chain_mcap.items(), key=lambda x: -x[1])
print("=== current mcap by platform (rough) ===")
for k, v in ranked[:20]:
    print(f"{k}: ${v/1e6:.2f}M")

# 3) inspect whether robinhood / x layer appear
for needle in ["robinhood", "x-layer", "xlayer", "okx", "binance", "bsc", "solana"]:
    hits = [k for k in chain_mcap if needle in k.lower()]
    print("needle", needle, hits)

# sample platforms for top coins
print("\n=== top 10 coin platforms ===")
for m in markets[:10]:
    cid = m["id"]
    plats = (by_id.get(cid) or {}).get("platforms") or {}
    print(m.get("symbol"), cid, "mcap", m.get("market_cap"), "plats", {k: v[:10] + "..." if v and len(v) > 10 else v for k, v in plats.items()})
