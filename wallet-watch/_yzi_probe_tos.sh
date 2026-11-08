#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
OUT=/mnt/e/Code/data-download/wallet-watch/_yzi_tos.txt
ssh -o ConnectTimeout=25 -o IdentitiesOnly=yes -i "$KEY" root@47.74.6.207 bash >"$OUT" 2>&1 <<'EOF'
cd /root/data-download
set -a
[ -f .env ] && . ./.env
[ -f /root/dashboard/.env.live ] && . /root/dashboard/.env.live
set +a
.venv/bin/python - <<'PY'
import json, os, urllib.parse, urllib.request, time
from collections import Counter
from datetime import datetime, timezone, timedelta

WATCH="0x877b3D5c681C8890d19dbF450306Caa3C3D4bba6"
ENA="0x57e114B691Db790C35207b2e685D4A43181e6061"
api=os.environ.get("ETHERSCAN_API_KEY","").strip()
print("key", bool(api))
params={
  "module":"account","action":"tokentx","contractaddress":ENA,
  "address":WATCH,"page":"1","offset":"100","sort":"desc"
}
if api:
    params["chainid"]="1"; params["apikey"]=api
    url="https://api.etherscan.io/v2/api?"+urllib.parse.urlencode(params)
else:
    url="https://eth.blockscout.com/api?"+urllib.parse.urlencode(params)
req=urllib.request.Request(url, headers={"User-Agent":"yzi-probe"})
time.sleep(0.5)
with urllib.request.urlopen(req, timeout=45) as r:
    data=json.loads(r.read().decode())
rows=data.get("result") or []
if not isinstance(rows, list):
    print("bad", data)
    raise SystemExit(1)
outs=[]
for row in rows:
    if str(row.get("from","")).lower()!=WATCH.lower(): continue
    outs.append((str(row.get("to","")).lower(), int(row.get("value") or 0)/1e18, int(row.get("timeStamp") or 0)))
print("outs", len(outs))
c=Counter(t for t,_,_ in outs)
for addr,n in c.most_common(12):
    amt=sum(a for t,a,_ in outs if t==addr)
    print(n, f"{amt/1e4:.0f}万", addr)
TZ=timezone(timedelta(hours=8))
oct13=[(t,a,ts) for t,a,ts in outs if datetime.fromtimestamp(ts,tz=TZ).strftime("%Y-%m-%d")=="2026-10-13"]
print("oct13 n", len(oct13), "ena万", sum(a for _,a,_ in oct13)/1e4)
print("oct13 tos", Counter(t for t,_,_ in oct13))
# compare to counterparties
cps=json.load(open("wallet-watch/counterparties.json"))
bn={k.lower() for k,v in (cps.get("ethereum") or {}).items() if "binance" in str(v).lower()}
print("hit known bn", sum(1 for t,_,_ in outs if t in bn), "of", len(outs))
print("oct13 hit", sum(1 for t,_,_ in oct13 if t in bn))
PY
EOF
cat "$OUT"
