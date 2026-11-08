"""Sum last 30 cached days. No Dune execute."""
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


def all_rows(qid: int) -> list[dict]:
    payload = get(f"https://api.dune.com/api/v1/query/{qid}/results?limit=1000")
    rows = list(payload.get("result", {}).get("rows") or [])
    while payload.get("next_uri"):
        payload = get(payload["next_uri"])
        rows.extend(payload.get("result", {}).get("rows") or [])
    print(qid, "n", len(rows), "ended", payload.get("execution_ended_at"))
    return rows


def day_of(value) -> str:
    return str(value)[:10]


rh = all_rows(8071940)
base = all_rows(8386348)
rh_days = {}
for row in rh:
    if row.get("series") != "stock trading (spot)":
        continue
    day = day_of(row.get("block_date"))
    rh_days[day] = rh_days.get(day, 0.0) + float(row.get("volume_usd") or 0)
base_days = {}
for row in base:
    day = day_of(row.get("day"))
    base_days[day] = base_days.get(day, 0.0) + float(row.get("volume_usd") or 0)
end = min(max(rh_days), max(base_days))
start = (date.fromisoformat(end) - timedelta(days=29)).isoformat()
rh_30 = sum(v for d, v in rh_days.items() if start <= d <= end)
base_30 = sum(v for d, v in base_days.items() if start <= d <= end)
print("window", start, end)
print("rh_30", round(rh_30), "last", end, round(rh_days.get(end, 0)))
print("base_30", round(base_30), "last", end, round(base_days.get(end, 0)))
print("rh_latest_own", max(rh_days), round(rh_days[max(rh_days)]))
print("base_latest_own", max(base_days), round(base_days[max(base_days)]))
