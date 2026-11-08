#!/usr/bin/env python3
import re
import urllib.request

url = "https://www.okx.com/zh-hans/proof-of-reserves/detail"
req = urllib.request.Request(
    url,
    headers={
        "User-Agent": "okx-por/1.0",
        "Accept": "text/html",
        "Accept-Language": "zh-CN,zh;q=0.9",
    },
)
with urllib.request.urlopen(req, timeout=60) as r:
    body = r.read().decode("utf-8", "replace")

print("len", len(body))
open("/tmp/okx_por.html", "w", encoding="utf-8").write(body)

for s in ["139", "储备", "account", "BTC", "__NEXT_DATA__", "por", "502299735", "账户资产", "reserveRatio"]:
    print("find", s, body.find(s))

idx = body.find("储备金率")
print("idx 储备金率", idx)
if idx >= 0:
    print(body[idx : idx + 1000])

for pat in [
    r"https://[^\"']+por[^\"']+",
    r"/priapi/[^\"']+",
    r"/v2/asset/[^\"']+",
    r"__NEXT_DATA__\s*=\s*(\{.+?\})\s*</script>",
]:
    ms = re.findall(pat, body)
    print("pat", pat[:40], "hits", len(ms))
    for m in ms[:5]:
        print(" ", m[:180] if isinstance(m, str) else m)

# dump script srcs
srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', body, re.I)
print("script srcs", len(srcs))
for s in srcs[:30]:
    if "por" in s.lower() or "asset" in s.lower() or "proof" in s.lower() or "chunk" in s.lower():
        print(" ", s)
