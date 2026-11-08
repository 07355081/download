"""Read cached RWA volume rows and sum recent stock spot. No execute."""
from __future__ import annotations

import json
import urllib.request
from datetime import date, timedelta
from pathlib import Path

key = None
for path in (Path("/root/dashboard/.env.live"), Path("/root/data-download/.env")):
    if not path.is_file():
        continue
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("DUNE_API_KEY="):
            key = line.split("=", 1)[1].strip().strip('"').strip("'")


def get(url: str):
    req = urllib.request.Request(url, headers={"X-Dune-API-Key": key})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode())


payload = get("https://api.dune.com/api/v1/query/8071940/results?limit=1000")
meta = payload.get("result", {}).get("metadata") or payload.get("execution_ended_at")
rows = payload.get("result", {}).get("rows") or []
print("rows", len(rows), "exec", payload.get("execution_ended_at") or payload.get("state"))
print("sample", rows[:2])
# paginate if next
while payload.get("next_uri") and len(rows) < 20000:
    payload = get(payload["next_uri"])
    rows.extend(payload.get("result", {}).get("rows") or [])
print("rows_all", len(rows))
days = sorted({str(r.get("block_date"))[:10] for r in rows})
print("day_range", days[0] if days else None, days[-1] if days else None, "n_days", len(days))
if days:
    end = date.fromisoformat(days[-1])
    start = end - timedelta(days=29)
    stock = 0.0
    by_day = {}
    for r in rows:
        day = str(r.get("block_date"))[:10]
        if day < start.isoformat() or day > end.isoformat():
            continue
        if r.get("series") != "stock trading (spot)":
            continue
        vol = float(r.get("volume_usd") or 0)
        stock += vol
        by_day[day] = by_day.get(day, 0) + vol
    print("stock_30d", round(stock, 2), "from", start, "to", end)
    if by_day:
        last = max(by_day)
        print("last_day", last, round(by_day[last], 2))
