# -*- coding: utf-8 -*-
import os
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
h = {"accept": "application/json", "User-Agent": "wublock"}
if key:
    h["x-cg-demo-api-key"] = key

for days in ("30", "90", "180"):
    r = requests.get(
        "https://api.coingecko.com/api/v3/exchanges/coinbase_international/volume_chart",
        headers=h,
        params={"days": days},
        timeout=60,
    )
    raw = r.json() if r.ok else []
    days_map = {}
    for pt in raw if isinstance(raw, list) else []:
        if isinstance(pt, list) and len(pt) >= 2 and pt[1] is not None:
            day = datetime.fromtimestamp(int(pt[0]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            days_map[day] = days_map.get(day, 0.0) + float(pt[1])
    oct_days = sorted(d for d in days_map if d.startswith("2026-10"))
    print("days=", days, "status", r.status_code, "raw", len(raw) if isinstance(raw, list) else raw, "uniq", len(days_map), "oct", len(oct_days), oct_days[:3], "...", oct_days[-3:])
