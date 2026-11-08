import re
import urllib.request


def get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "verify"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


zh = get("https://data.wublock123.com/dashboard/lark-feeds/hl-net-revenue")
en = get("https://data.wublockchain.xyz/dashboard/lark-feeds/hl-net-revenue")
print("zh hyper", "Hyperliquid" in zh, "suolana", zh.count("索拉纳"), "missing", "未找到" in zh)
print("en 30day", "30-day" in en, "missing", "Feed not found" in en, "suolana", en.count("索拉纳"))
srcs = sorted(set(re.findall(r"/_next/static/chunks/[^\"']+hlrev\.js", zh)))
print("srcs", srcs)
for src in srcs:
    js = get("https://data.wublock123.com" + src)
    print(src.split("/")[-1], len(js), "slug", js.count("hl-net-revenue"), "solana", "solana-quarter" in js)
