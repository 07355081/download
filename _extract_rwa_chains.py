#!/usr/bin/env python3
"""Extract stocks-equities chain / 30d figures from DefiLlama SSR HTML + CoinGecko sample."""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

UA = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=90) as resp:
        return resp.read()


html = get("https://defillama.com/rwa/category/stocks-equities").decode("utf-8", "replace")
Path("/tmp/stocks_equities.html").write_text(html, encoding="utf-8")
print("html", len(html))

# dump windows around key chains
for key in ["Robinhood Chain", "X Layer", "BSC", "Solana", "Ethereum", "Base"]:
    i = html.find(key)
    print("\n====", key, "at", i)
    if i >= 0:
        print(html[max(0, i - 120) : i + 280].replace("\n", " ")[:450])

# find JSON blobs that look like chart series
# common pattern: "name":"Robinhood Chain"
for m in re.finditer(r'\{"name":"(Robinhood Chain|X Layer|BSC|BNB Chain|Solana|Ethereum|Base)"[^\}]{0,400}\}', html):
    print("jsonish", m.group(0)[:400])

# broader: search 30d / change30d near chain names
for pat in [
    r'"chain"\s*:\s*"(Robinhood Chain|X Layer|BSC|BNB[^"]*)"[^,]{0,80}',
    r'"(Robinhood Chain|X Layer)"\s*:\s*\{[^\}]{0,300}\}',
    r'change30d|change_30d|netFlow30d|flows30d|["\']30d["\']',
]:
    hits = re.findall(pat, html)
    print("pat", pat[:50], "n=", len(hits), "sample", hits[:8])

# Try to find __NEXT_DATA__ or large JSON assignment
for pat in [
    r'<script id="__NEXT_DATA__"[^>]*>(\{.*?\})</script>',
    r'window\.__NUXT__\s*=',
    r'self\.__next_f\.push',
]:
    print("embed", pat[:40], bool(re.search(pat, html, re.S)))

# Extract RSC flight payloads that contain chain + numbers
# Look for sequences like Robinhood Chain ... 129 or million
chunk_re = re.compile(
    r"(Robinhood Chain|X Layer|BSC)(.{0,800}?)(1\d{2}\.\d|\$?\d+\.?\d*\s*[mb]illion|\$\d[\d,\.]*)",
    re.I | re.S,
)
seen = set()
for m in chunk_re.finditer(html):
    key = (m.group(1), m.group(3)[:40])
    if key in seen:
        continue
    seen.add(key)
    if len(seen) <= 25:
        print("chunk", m.group(1), "->", m.group(3)[:60], "| ctx", re.sub(r"\s+", " ", m.group(2))[-80:])

print("\n=== CoinGecko tokenized-stock top (no chain) ===")
raw = get(
    "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&category=tokenized-stock&order=market_cap_desc&per_page=15&page=1"
)
rows = json.loads(raw)
for r in rows:
    print(
        r.get("symbol"),
        r.get("name"),
        "mcap=",
        r.get("market_cap"),
        "chg30d=",
        (r.get("price_change_percentage_30d_in_currency")),
    )

# RH rwa_aum payload
print("\n=== RH rwa_aum payload ===")
p = Path("/root/dashboard/public/json/robinhood-chain/rwa_aum.json")
doc = json.loads(p.read_text(encoding="utf-8"))
print({k: doc.get(k) for k in list(doc)[:15]})
rows = doc.get("rows") or doc.get("data") or doc.get("result")
print("rows type", type(rows), "len", len(rows) if hasattr(rows, "__len__") else None)
if isinstance(rows, list) and rows:
    print("sample", rows[0], rows[-1])
