#!/usr/bin/env python3
"""Probe free sources for tokenized stocks mcap by chain."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent / "_probe_rwa_free_out.txt"
UA = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}


def get(url: str, timeout: int = 60) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def try_get(url: str) -> str:
    try:
        status, body = get(url)
        return f"OK {status} {url} len={len(body)} head={body[:180]!r}"
    except Exception as e:
        return f"FAIL {url} {type(e).__name__}: {e}"


lines: list[str] = []

# --- free rwa/stats ---
lines.append("=== rwa/stats ===")
try:
    _, raw = get("https://api.llama.fi/rwa/stats")
    data = json.loads(raw)
    lines.append(f"type={type(data)} n={len(data)}")
    if isinstance(data, dict):
        k = next(iter(data))
        lines.append(f"sample {k}: {json.dumps(data[k], ensure_ascii=False)[:500]}")
        cats = defaultdict(int)
        for v in data.values():
            if isinstance(v, dict):
                for key in ("category", "assetClass", "type", "sector"):
                    if v.get(key):
                        cats[f"{key}={v.get(key)}"] += 1
        lines.append(f"field freqs: {dict(list(cats.items())[:30])}")
except Exception as e:
    lines.append(f"rwa/stats err {e}")

# --- page scrape stocks-equities ---
lines.append("\n=== stocks-equities HTML ===")
try:
    _, html_b = get("https://defillama.com/rwa/category/stocks-equities")
    html = html_b.decode("utf-8", "replace")
    lines.append(f"html len={len(html)}")
    for pat in [
        r"https://api\.llama\.fi[^\"']+",
        r"https://pro-api\.llama\.fi[^\"']+",
        r"/rwa/[^\"']{3,80}",
        r"chain-breakdown",
        r"stocks-equities",
        r"Robinhood Chain",
        r"X Layer",
        r"BSC|BNB",
    ]:
        hits = sorted(set(re.findall(pat, html)))[:20]
        lines.append(f"pat {pat}: {hits}")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(\{.*?\})</script>', html, re.S)
    lines.append(f"__NEXT_DATA__={bool(m)}")
    if m:
        Path(__file__).resolve().parent.joinpath("_tmp_rwa_next.json").write_text(
            m.group(1)[:800000], encoding="utf-8"
        )
except Exception as e:
    lines.append(f"html err {e}")

# --- candidate endpoints ---
lines.append("\n=== candidates ===")
cands = [
    "https://api.llama.fi/rwa/current",
    "https://api.llama.fi/rwa/stats",
    "https://api.llama.fi/rwa/list",
    "https://api.llama.fi/rwa/chart/chain-breakdown",
    "https://api.llama.fi/rwa/chart/chain/BSC",
    "https://api.llama.fi/rwa/chain/BSC",
    "https://api.llama.fi/rwa/chain/X Layer",
    "https://api.llama.fi/equities/v1/onchain",
    "https://api.llama.fi/equities/v1/companies-list",
    "https://coins.llama.fi/prices/current/coingecko:apple",
    "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&category=tokenized-stock&order=market_cap_desc&per_page=20&page=1",
    "https://api.rwa.xyz/v3/assets",
    "https://api.rwa.xyz/v1/platforms",
]
for u in cands:
    lines.append(try_get(u))

# --- protocol TVL for major stock issuers: historical chainTvls ---
lines.append("\n=== protocol chain TVL (proxy, not necessarily stock AUM) ===")
for slug in ["xstocks", "robinhood", "ondo-finance", "backedu", "dinari"]:
    try:
        _, raw = get(f"https://api.llama.fi/protocol/{slug}")
        doc = json.loads(raw)
        ctvls = doc.get("currentChainTvls") or {}
        lines.append(f"{slug} name={doc.get('name')} currentChainTvls={ctvls} tvl={doc.get('tvl') and 'series'}")
        # last point of chainTvls series if any
        chain_tvls = doc.get("chainTvls") or {}
        summary = {}
        for chain, payload in list(chain_tvls.items())[:12]:
            series = (payload or {}).get("tvl") or []
            if series:
                summary[chain] = series[-1]
        lines.append(f"  last chain points: {summary}")
    except Exception as e:
        lines.append(f"{slug} FAIL {e}")

OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"wrote {OUT} lines={len(lines)}")
