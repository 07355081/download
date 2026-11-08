#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=20 "$HOST" 'cd /root/data-download && .venv/bin/python - <<'"'"'PY'"'"'
import json, os, urllib.request
from pathlib import Path
key=None
for p in [Path("/root/dashboard/.env.live"), Path("/root/data-download/.env")]:
    if not p.is_file():
        continue
    for line in p.read_text(errors="replace").splitlines():
        if line.startswith("DUNE_API_KEY="):
            key=line.split("=",1)[1].strip().strip("\"'")
if not key:
    raise SystemExit("no key")
def get(url):
    req=urllib.request.Request(url, headers={"X-Dune-API-Key": key})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())
q=get("https://api.dune.com/api/v1/query/8071940")
sql=q.get("query_sql") or ""
print("QUERY_LEN", len(sql))
print(sql[:4000])
print("---USAGE---")
try:
    u=get("https://api.dune.com/api/v1/usage")
    print(json.dumps(u)[:1500])
except Exception as e:
    print("usage", type(e).__name__, e)
PY'
