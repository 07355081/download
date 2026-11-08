#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=20 "$HOST" bash -s <<'REMOTE'
set -euo pipefail
echo '=== keys ==='
grep -E 'TOKEN_TERMINAL|RWA_|HELIUS|SOLSCAN|BIRDEYE' /root/dashboard/.env.live /root/data-download/.env 2>/dev/null | sed 's/=.*/=***/' || echo none
echo '=== probe ==='
python3 - <<'PY'
import json, urllib.request
UA={"User-Agent":"Mozilla/5.0","Accept":"application/json"}
mints={"SPYx":"XsoCS1TfEyfFhfvj8EtZ528L3CaKBDBRqRapnBbDF2W","QQQx":"Xs8S1uUs1zvS2p7iwtsG3b6fkhpvmwz4GYU3gWAmWHZ"}

def get(url, extra=None):
    h=dict(UA)
    if extra: h.update(extra)
    req=urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.status, r.read()[:900]

for name,mint in mints.items():
    for url in [
        f"https://lite-api.jup.ag/tokens/v2/search?query={name}",
        f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
        f"https://api.solana.fm/v0/tokens/{mint}/holders?limit=1",
    ]:
        try:
            st,b=get(url)
            print(name, st, url.split("/")[2], b[:220].replace(b"\n", b" "))
        except Exception as e:
            print(name, "FAIL", url.split("/")[2], type(e).__name__, str(e)[:120])

# solscan pro-less?
for name,mint in mints.items():
    url=f"https://api-v2.solscan.io/v2/token?address={mint}"
    try:
        st,b=get(url)
        print("solscan", name, st, b[:300])
    except Exception as e:
        print("solscan FAIL", name, e)

# public solana rpc cannot cheaply count holders
rpc="https://api.mainnet-beta.solana.com"
body=json.dumps({"jsonrpc":"2.0","id":1,"method":"getTokenSupply","params":[mints["SPYx"]]}).encode()
req=urllib.request.Request(rpc, data=body, headers={"Content-Type":"application/json"})
with urllib.request.urlopen(req, timeout=20) as r:
    print("rpc supply", r.read()[:250])
PY
REMOTE
echo PROBE_DONE
