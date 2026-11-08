# -*- coding: utf-8 -*-
import os
import time
from pathlib import Path

import requests

for p in (Path(__file__).resolve().parent.parent / ".env", Path("/root/dashboard/.env.live")):
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

key = (os.environ.get("COINGECKO_DEMO_API_KEY") or os.environ.get("COINGECKO_KEY") or "").strip()
h = {"accept": "application/json", "User-Agent": "wublock"}
if key:
    h["x-cg-demo-api-key"] = key
    print("key", "yes", len(key))
else:
    print("key no")

BASE = "https://api.coingecko.com/api/v3"
eid = "coinbase_international"
time.sleep(2)
r = requests.get(f"{BASE}/exchanges/{eid}", headers=h, timeout=60)
j = r.json()
print("status", r.status_code)
for k in (
    "name",
    "trust_score",
    "trust_score_rank",
    "trade_volume_24h_btc",
    "trade_volume_24h_btc_normalized",
    "centralized",
):
    print(k, j.get(k))
tickers = j.get("tickers") or []
print("tickers_in_payload", len(tickers))
if tickers:
    print("sample_ticker", {k: tickers[0].get(k) for k in ("base", "target", "volume", "converted_volume", "bid_ask_spread_percentage", "last_traded_at", "timestamp")})

time.sleep(2)
r2 = requests.get(f"{BASE}/exchanges/{eid}/tickers", headers=h, params={"page": 1}, timeout=60)
print("tickers_endpoint", r2.status_code)
if r2.ok:
    jj = r2.json()
    ts = jj.get("tickers") or []
    print("tickers_page1", len(ts))
    if ts:
        t0 = ts[0]
        print("t0", t0.get("base"), t0.get("target"), "vol", t0.get("volume"), "last_traded", t0.get("last_traded_at"), "ts", t0.get("timestamp"))
