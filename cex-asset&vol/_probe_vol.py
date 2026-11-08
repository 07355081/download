# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
for p in (BASE.parent / ".env", Path("/root/dashboard/.env.live")):
    if not p.is_file():
        continue
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

key = (
    os.environ.get("COINGECKO_DEMO_API_KEY")
    or os.environ.get("COINGECKO_KEY")
    or os.environ.get("COINGECKO_API_KEY")
    or ""
).strip()
print("key", bool(key), len(key))
headers = {"accept": "application/json", "User-Agent": "wublock-probe"}
if key:
    headers["x-cg-demo-api-key"] = key
for i in ["okex", "okx", "bybit", "bybit_spot", "huobi", "gdax", "gate"]:
    url = f"https://api.coingecko.com/api/v3/exchanges/{i}/volume_chart"
    try:
        r = requests.get(url, headers=headers, params={"days": 30}, timeout=30)
        print(i, r.status_code, "len", len(r.json()) if r.ok else r.text[:120])
    except Exception as e:
        print(i, "ERR", e)
