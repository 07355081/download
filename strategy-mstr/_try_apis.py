"""Try known Strategy API endpoints."""
from __future__ import annotations

import json
import ssl
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "cache"
CTX = ssl.create_default_context()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

PATHS = [
    "https://api.strategy.com/btc/bitcoinKpis",
    "https://api.strategy.com/btc/mstrKpiData",
    "https://api.strategy.com/bitcoinKpis",
    "https://api.strategy.com/mstrKpiData",
    "https://api.strategy.com/btc/history",
    "https://api.strategy.com/btc/mstrHistory",
    "https://api.strategy.com/btc/kpiHistory",
    "https://api.strategy.com/btc/metrics",
    "https://api.strategy.com/btc/download",
    "https://api.strategy.com/btc/mstrKpiHistory",
    "https://api.strategy.com/btc/timeseries",
    "https://www.strategy.com/api/btc/mstrKpiData",
    "https://www.strategy.com/_next/data/6a80fbe95a677e96f5ce1e7c/index.json",
]


def fetch(url: str) -> None:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://www.strategy.com/",
            "Origin": "https://www.strategy.com",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=CTX) as resp:
            body = resp.read()
            ctype = resp.headers.get("content-type", "")
            print(f"OK {resp.status} {len(body):7d} {ctype:40s} {url}")
            name = url.replace("https://", "").replace("/", "_")[-80:]
            (OUT / f"resp_{name}").write_bytes(body[:2_000_000])
            if "json" in ctype or body[:1] in (b"{", b"["):
                try:
                    data = json.loads(body)
                    if isinstance(data, dict):
                        print("  keys:", list(data.keys())[:40])
                    elif isinstance(data, list):
                        print("  list", len(data), "first", str(data[0])[:200] if data else None)
                except Exception as exc:  # noqa: BLE001
                    print("  json parse fail", exc)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {url} {exc}")


if __name__ == "__main__":
    for p in PATHS:
        fetch(p)
