#!/bin/bash
KEY=/root/.ssh/data-dashboard-prod
ssh -i "$KEY" -o IdentitiesOnly=yes root@47.74.6.207 'python3 - <<"PY"
import json, urllib.request
UA={"User-Agent":"wu/1","Content-Type":"application/json"}
def info(body):
    req=urllib.request.Request("https://api.hyperliquid.xyz/info", data=json.dumps(body).encode(), headers=UA, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())
# list perp dexs
try:
    dexs=info({"type":"perpDexs"})
except Exception as e:
    print("perpDexs FAIL", e); raise
print("dexs_n", len(dexs) if isinstance(dexs,list) else type(dexs))
# first item often null = main crypto
names=[]
for d in dexs:
    if d is None:
        names.append("")
    elif isinstance(d, dict):
        names.append(str(d.get("name") or ""))
print("names", names[:20], "... total", len(names))

def oi_usd(dex_name):
    payload=info({"type":"metaAndAssetCtxs", "dex": dex_name} if dex_name else {"type":"metaAndAssetCtxs"})
    meta, ctxs = payload[0], payload[1]
    universe=meta.get("universe") or []
    total=0.0
    for u,c in zip(universe, ctxs):
        try:
            total += float(c.get("openInterest") or 0) * float(c.get("markPx") or 0)
        except Exception:
            pass
    return total, len(universe)

crypto, n_c = oi_usd("")
print("crypto_oi_B", round(crypto/1e9,3), "n", n_c)
hip3=0.0; n_h=0
for name in names:
    if not name:
        continue
    oi, n = oi_usd(name)
    hip3 += oi; n_h += n
    print(" ", name, round(oi/1e9,3))
print("hip3_oi_B", round(hip3/1e9,3), "markets", n_h)
total=crypto+hip3
print("share_pct", round(hip3/total*100,2) if total else None, "total_B", round(total/1e9,3))
PY'
