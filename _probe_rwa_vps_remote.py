#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 rwa-probe", "Accept": "*/*"}


def get(url: str, timeout: int = 45) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def try_get(url: str) -> None:
    try:
        st, body = get(url)
        print(f"OK {st} {url} len={len(body)} head={body[:160]!r}")
    except Exception as e:
        print(f"FAIL {url} {type(e).__name__}: {e}")


print("=== candidates ===")
for u in [
    "https://api.llama.fi/rwa/current",
    "https://api.llama.fi/rwa/stats",
    "https://api.llama.fi/rwa/chart/chain-breakdown",
    "https://api.llama.fi/rwa/list",
    "https://api.llama.fi/equities/v1/onchain",
    "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&category=tokenized-stock&order=market_cap_desc&per_page=10&page=1",
    "https://api.coingecko.com/api/v3/coins/categories/list",
]:
    try_get(u)

print("\n=== stocks-equities page APIs ===")
try:
    _, html_b = get("https://defillama.com/rwa/category/stocks-equities")
    html = html_b.decode("utf-8", "replace")
    print("html len", len(html))
    urls = sorted(
        set(
            re.findall(r"https://(?:api|pro-api)\.llama\.fi[^\"'\s]+", html)
            + re.findall(r"/_next/data/[^\"']+", html)
        )
    )
    for u in urls[:30]:
        print("found", u)
    # look for embedded JSON with chain movers
    for key in ["Robinhood Chain", "X Layer", "BSC", "BNB Chain", "stocks-equities", "30d"]:
        print("find", key, html.find(key))
except Exception as e:
    print("html fail", e)

print("\n=== try known slug protocol histories for stock-ish ===")
for slug in [
    "xstocks",
    "ondo-global-markets",
    "dinari",
    "prestocks",
    "backedu",
    "backed-finance-rwa",
]:
    try:
        st, raw = get(f"https://api.llama.fi/protocol/{slug}")
        doc = json.loads(raw)
        ctvls = doc.get("currentChainTvls") or {}
        print(slug, "name=", doc.get("name"), "chains=", ctvls)
    except Exception as e:
        print(slug, "FAIL", e)

# existing RH rwa_aum on VPS?
print("\n=== local RH rwa_aum ===")
from pathlib import Path

for p in [
    Path("/root/data-download/robinhood-chain/output/json/rwa_aum.json"),
    Path("/root/dashboard/public/json/robinhood-chain/rwa_aum.json"),
]:
    print(p, "exists", p.is_file(), "size", p.stat().st_size if p.is_file() else 0)
    if p.is_file():
        doc = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(doc, list):
            print("  rows", len(doc), "sample", doc[-1] if doc else None)
        elif isinstance(doc, dict):
            print("  keys", list(doc)[:10])
