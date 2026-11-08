import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

p = Path("/root/data-download/.dune-credit-ledger.json")
d = json.loads(p.read_text(encoding="utf-8"))
entries = d if isinstance(d, list) else d.get("entries") or []
print("entries", len(entries))
now = datetime.now(timezone.utc)
print("now", now.isoformat())
for e in entries:
    print(e)
    at = e.get("at") or e.get("ts")
    credits = e.get("credits")
    if not at or not isinstance(credits, (int, float)):
        continue
    t = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    age = (now - t).total_seconds() / 86400
    print(f"  age_days={age:.2f} credits={credits} free_14d={(t + timedelta(days=14)).isoformat()}")
