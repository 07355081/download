#!/usr/bin/env python3
import json
from pathlib import Path

p = Path("/root/dashboard/public/json/robinhood-chain/rwa_aum.json")
doc = json.loads(p.read_text(encoding="utf-8"))
print("keys", list(doc.keys()))
for k, v in doc.items():
    if k in ("rows", "result", "data", "records"):
        print(k, type(v), len(v) if hasattr(v, "__len__") else v)
        if isinstance(v, list) and v:
            print(" first", v[0])
            print(" last", v[-1])
    else:
        print(k, ":", str(v)[:200])
