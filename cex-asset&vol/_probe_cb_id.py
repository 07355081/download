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

r = requests.get("https://api.coingecko.com/api/v3/exchanges/list", headers=h, timeout=60)
r.raise_for_status()
items = r.json()
hits = [
    x
    for x in items
    if "coinbase" in x.get("id", "").lower() or "coinbase" in x.get("name", "").lower()
]
print("hits", hits)
for eid in [x["id"] for x in hits]:
    time.sleep(1.5)
    rr = requests.get(
        f"https://api.coingecko.com/api/v3/exchanges/{eid}/volume_chart",
        headers=h,
        params={"days": "30"},
        timeout=60,
    )
    body = rr.json() if rr.ok else rr.text[:120]
    print(eid, rr.status_code, "pts", len(body) if isinstance(body, list) else body)
