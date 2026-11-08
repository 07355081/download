"""Read public Coinbase Base stock-token queries. No execute."""
from __future__ import annotations

import json
import urllib.request
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


for qid in (8386348, 8430370):
    print("====", qid)
    try:
        meta = get(f"https://api.dune.com/api/v1/query/{qid}")
        sql = meta.get("query_sql") or ""
        print("name", meta.get("name"), "len", len(sql))
        print(sql[:1800])
    except Exception as exc:  # noqa: BLE001
        print("meta", type(exc).__name__, exc)
        continue
    try:
        payload = get(f"https://api.dune.com/api/v1/query/{qid}/results?limit=5")
        rows = payload.get("result", {}).get("rows") or []
        print("exec", payload.get("execution_ended_at"), "sample", rows[:2], "n", len(rows))
    except Exception as exc:  # noqa: BLE001
        print("results", type(exc).__name__, exc)
