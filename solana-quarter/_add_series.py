#!/usr/bin/env python3
import json
from pathlib import Path

path = Path("/root/dashboard/public/json/solana-quarter/solana_quarter.json")
doc = json.loads(path.read_text(encoding="utf-8"))
quarters = [row for row in doc.get("quarters") or [] if isinstance(row, dict)]
labels = [
    ("Solana 应用收入", "Solana application revenue", "app_revenue_usd"),
    ("Pump.fun 收入", "Pump.fun revenue", "pump_revenue_usd"),
    ("Solana 季末 DeFi TVL", "Solana quarter-end DeFi TVL", "tvl_end_usd"),
    ("OnRe 季末 TVL", "OnRe quarter-end TVL", "onre_end_usd"),
]

def series(label, label_en, key):
    months = []
    for row in quarters:
        value = row.get(key)
        if isinstance(value, (int, float)) and value > 0:
            months.append({"month": row["id"], "usd": value})
    return {"label": label, "labelEn": label_en, "slug": key, "months": months}

doc["series"] = [series(*item) for item in labels]
path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("ok", [(row["label"], row["labelEn"]) for row in doc["series"]])
