# -*- coding: utf-8 -*-
"""Distinguish CG lag vs Coinbase International actually dark."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
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
h = {"accept": "application/json", "User-Agent": "wublock-probe"}
if key:
    h["x-cg-demo-api-key"] = key

CG = "https://api.coingecko.com/api/v3"
now = datetime.now(timezone.utc)
print("now_utc", now.isoformat())

# 1) CG list entry status
r = requests.get(f"{CG}/exchanges/list", headers=h, timeout=60)
r.raise_for_status()
hits = [x for x in r.json() if "coinbase" in str(x).lower()]
print("cg_list_coinbase", hits)

time.sleep(1.5)
r = requests.get(f"{CG}/exchanges", headers=h, params={"per_page": 250, "page": 1}, timeout=60)
# may not include low-rank; search pages briefly
found = None
for page in range(1, 6):
    time.sleep(1.2)
    rr = requests.get(f"{CG}/exchanges", headers=h, params={"per_page": 250, "page": page}, timeout=60)
    if not rr.ok:
        print("exchanges page", page, rr.status_code)
        break
    for x in rr.json():
        if x.get("id") == "coinbase_international":
            found = x
            break
    if found:
        break
print("cg_exchanges_page_hit", found)

time.sleep(1.5)
meta = requests.get(f"{CG}/exchanges/coinbase_international", headers=h, timeout=60).json()
print(
    "cg_meta",
    {
        "name": meta.get("name"),
        "trust_score": meta.get("trust_score"),
        "trust_score_rank": meta.get("trust_score_rank"),
        "trade_volume_24h_btc": meta.get("trade_volume_24h_btc"),
        "tickers_len": len(meta.get("tickers") or []),
        "url": meta.get("url"),
        "other_url_1": (meta.get("other_url_1") or [None])[0] if isinstance(meta.get("other_url_1"), list) else meta.get("other_url_1"),
    },
)

# 2) Compare lag pattern: healthy exchange tip vs CB intl tip
for eid in ("gdax", "okex_swap", "bybit", "coinbase_international"):
    time.sleep(1.5)
    rr = requests.get(
        f"{CG}/exchanges/{eid}/volume_chart",
        headers=h,
        params={"days": "30"},
        timeout=60,
    )
    if not rr.ok:
        print(eid, "chart", rr.status_code)
        continue
    raw = rr.json()
    days = []
    for pt in raw if isinstance(raw, list) else []:
        if isinstance(pt, list) and len(pt) >= 2 and pt[1] is not None:
            days.append(datetime.fromtimestamp(int(pt[0]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d"))
    days = sorted(set(days))
    tip = days[-1] if days else None
    lag = (now.date() - datetime.strptime(tip, "%Y-%m-%d").date()).days if tip else None
    print(f"chart_tip {eid}: last={tip} lag_days={lag} n={len(days)}")

# 3) Is Coinbase International website / public API reachable?
for url, label in (
    ("https://www.coinbase.com/international", "cb_intl_web"),
    ("https://api.international.coinbase.com/api/v1/instruments", "cb_intl_instruments"),
    ("https://api.exchange.coinbase.com/products", "cb_spot_products"),
):
    try:
        rr = requests.get(url, headers={"User-Agent": "wublock-probe"}, timeout=20, allow_redirects=True)
        body = rr.text[:120].replace("\n", " ")
        print(label, "status", rr.status_code, "final", rr.url, "body", body)
    except Exception as e:
        print(label, "ERR", type(e).__name__, e)
