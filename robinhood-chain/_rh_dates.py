import json
from pathlib import Path
for name in ["daily_transactions", "active_wallets", "dex_volume"]:
    p = Path("/root/data-download/robinhood-chain/output/json") / f"{name}.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    rows = d.get("data") or []
    days = sorted({str(r.get("day"))[:10] for r in rows if isinstance(r, dict) and r.get("day")})
    print(name, "last3=", days[-3:] if days else None, "updated=", d.get("updated_at"), "qid=", d.get("query_id"))
