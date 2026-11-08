#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, urllib.request

UA = {"User-Agent": "dashboard-data/probe", "Content-Type": "application/json", "Accept": "application/json"}
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"

# Aave v3 GraphQL — try a few query shapes
queries = [
    """query {
      market(request: { chainId: 1, market: "proto_mainnet_v3" }) {
        reserves {
          underlyingToken { symbol address }
          size { usd amount }
          availableLiquidity { usd amount }
          totalDebt { usd amount }
          supplyApy
          borrowApy
        }
      }
    }""",
    """query {
      reserves(request: { chainIds: [1] }) {
        underlyingToken { symbol }
        availableLiquidity { usd }
      }
    }""",
]

for i, q in enumerate(queries):
    try:
        body = json.dumps({"query": q}).encode()
        req = urllib.request.Request("https://api.v3.aave.com/graphql", data=body, headers=UA, method="POST")
        with urllib.request.urlopen(req, timeout=40) as r:
            doc = json.loads(r.read())
        print("Q", i, "keys", list(doc.keys()))
        if doc.get("errors"):
            print("  errors", str(doc["errors"])[:400])
        data = doc.get("data")
        print("  data", str(data)[:500] if data else None)
    except Exception as e:
        print("Q", i, "FAIL", type(e).__name__, e)

# DefiLlama aave-v3 tokensInUsd WETH history tip
req = urllib.request.Request("https://api.llama.fi/protocol/aave-v3", headers={"User-Agent": "dashboard-data/probe"})
with urllib.request.urlopen(req, timeout=90) as r:
    doc = json.loads(r.read())
print("llama keys", list(doc.keys())[:20])
tiu = doc.get("tokensInUsd") or []
print("tokensInUsd n", len(tiu))
if tiu:
    last = tiu[-1]
    toks = last.get("tokens") or {}
    weth_keys = [k for k in toks if "weth" in k.lower() or k.lower() == "ethereum"]
    print("last date", last.get("date"), "weth-like", {k: toks[k] for k in weth_keys[:10]})
    # find mid april 2026 approx
    from datetime import datetime, timezone
    for row in tiu:
        day = datetime.fromtimestamp(int(row["date"]), tz=timezone.utc).strftime("%Y-%m-%d")
        if day.startswith("2026-04-1") or day.startswith("2026-04-2"):
            t = row.get("tokens") or {}
            wk = [k for k in t if "weth" in k.lower()]
            if wk:
                print(day, {k: t[k] for k in wk[:5]})
