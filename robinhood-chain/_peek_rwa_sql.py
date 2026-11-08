"""Print Dune query 8071940 SQL and usage. Run on VPS."""
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
if not key:
    raise SystemExit("no key")


def get(url: str):
    req = urllib.request.Request(url, headers={"X-Dune-API-Key": key})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


query = get("https://api.dune.com/api/v1/query/8071940")
sql = query.get("query_sql") or ""
print("QUERY_LEN", len(sql))
print(sql[:6000])
print("---USAGE---")
try:
    usage = get("https://api.dune.com/api/v1/usage")
    print(json.dumps(usage)[:2000])
except Exception as exc:  # noqa: BLE001
    print("usage", type(exc).__name__, exc)
