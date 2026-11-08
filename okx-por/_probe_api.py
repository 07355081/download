#!/usr/bin/env python3
import json
import re
import urllib.request

UA = {"User-Agent": "okx-por/1.0", "Accept": "*/*"}


def get(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


html = open("/tmp/okx_por.html", encoding="utf-8").read()
print("=== around 账户资产 ===")
i = html.find("账户资产")
print(html[i - 200 : i + 500])

print("\n=== around 139 ===")
i = html.find("139")
print(html[max(0, i - 150) : i + 250])

print("\n=== around report id ===")
i = html.find("502299735")
print(html[max(0, i - 200) : i + 400])

# JS bundle named proofOfReservesDetail
srcs = re.findall(r'src="(https://www\.okx\.com/cdn/assets/okfe/balance/contentweb/[^"]+)"', html)
print("\nbalance scripts", len(srcs))
for s in srcs:
    print(s)

js_url = None
for s in srcs:
    if "proofOfReservesDetail" in s:
        js_url = s
        break
if not js_url and srcs:
    js_url = srcs[-1]

if js_url:
    print("\n=== fetching", js_url)
    js = get(js_url)
    open("/tmp/okx_por_detail.js", "w", encoding="utf-8").write(js)
    print("js len", len(js))
    for pat in [
        r"/priapi/[^\"'`]+",
        r"/v2/[^\"'`]*por[^\"'`]*",
        r"por[^\"'`]{0,40}",
        r"reserveRatio",
        r"accountAssets",
        r"liability",
    ]:
        hits = sorted(set(re.findall(pat, js)))
        print("pat", pat, "n=", len(hits))
        for h in hits[:20]:
            print(" ", h[:200])

# also try common APIs with report id
candidates = [
    "https://www.okx.com/priapi/v5/asset/por/detail?reportId=502299735",
    "https://www.okx.com/priapi/v5/asset/por/currency?reportId=502299735",
    "https://www.okx.com/priapi/v5/balance/por/detail?reportId=502299735",
    "https://www.okx.com/v2/asset/balance/por-detail?reportId=502299735",
    "https://www.okx.com/priapi/v5/asset/por/projects",
    "https://www.okx.com/priapi/v5/asset/por/project",
]
print("\n=== API probes ===")
for url in candidates:
    try:
        body = get(url)
        print(url, "OK", body[:180].replace("\n", " "))
    except Exception as e:
        print(url, "FAIL", type(e).__name__, e)
