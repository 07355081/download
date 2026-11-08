# -*- coding: utf-8 -*-
"""Why coinbase_international volume_chart stops mid-October."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
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

BASE = "https://api.coingecko.com/api/v3"


def daily_map(eid: str, days: str) -> dict[str, float]:
    r = requests.get(
        f"{BASE}/exchanges/{eid}/volume_chart",
        headers=h,
        params={"days": days},
        timeout=60,
    )
    print(f"\n=== {eid} days={days} status={r.status_code}")
    if not r.ok:
        print(r.text[:200])
        return {}
    raw = r.json()
    out: dict[str, float] = {}
    if not isinstance(raw, list):
        print("unexpected", type(raw), raw)
        return {}
    for pt in raw:
        if isinstance(pt, list) and len(pt) >= 2 and pt[1] is not None:
            day = datetime.fromtimestamp(int(pt[0]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            out[day] = out.get(day, 0.0) + float(pt[1])
    if out:
        keys = sorted(out)
        print(f"raw_pts={len(raw)} uniq_days={len(out)} range={keys[0]}..{keys[-1]}")
        oct_ = [k for k in keys if k.startswith("2026-10")]
        print(f"oct days={len(oct_)} first={oct_[:3]} last={oct_[-3:] if oct_ else None}")
    else:
        print("empty daily map")
    return out


# 1) exchange metadata
for eid in (
    "coinbase_international",
    "coinbase_international_derivatives",
    "gdax",
    "binance_futures",
):
    time.sleep(1.2)
    r = requests.get(f"{BASE}/exchanges/{eid}", headers=h, timeout=60)
    if r.status_code == 404:
        print(f"meta {eid}: 404")
        continue
    if not r.ok:
        print(f"meta {eid}: {r.status_code} {r.text[:120]}")
        continue
    j = r.json()
    print(
        f"meta {eid}: name={j.get('name')!r} year={j.get('year_established')} "
        f"trust={j.get('trust_score')} rank={j.get('trust_score_rank')} "
        f"trade_vol_24h_btc={j.get('trade_volume_24h_btc')} "
        f"has_trading_incentive={j.get('has_trading_incentive')}"
    )

# 2) volume chart coverage
for eid in ("coinbase_international", "gdax", "okex_swap", "binance"):
    time.sleep(1.5)
    daily_map(eid, "90")

# 3) local CSV history for old/new columns
from _paths import MAJOR_VOL_CSV  # noqa: E402

df = pd.read_csv(MAJOR_VOL_CSV, encoding="utf-8-sig")
df["_d"] = pd.to_datetime(df.iloc[:, 0])
for col in ("coinbase_international_derivatives", "coinbase_international", "gdax"):
    if col not in df.columns:
        print(f"csv missing {col}")
        continue
    s = df[["_d", col]].dropna()
    s = s[pd.to_numeric(s[col], errors="coerce").fillna(0) > 0]
    print(f"csv {col}: n={len(s)}", end=" ")
    if len(s):
        print(f"range={s['_d'].min().date()}..{s['_d'].max().date()}")
        oct_ = s[(s["_d"] >= "2026-10-01") & (s["_d"] <= "2026-10-31")]
        print(f"  oct n={len(oct_)} last={oct_['_d'].max().date() if len(oct_) else None}")
    else:
        print("empty")
