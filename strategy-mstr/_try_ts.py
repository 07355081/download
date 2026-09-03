"""Fetch timeSeries and related history APIs."""
from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
CTX = ssl.create_default_context()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
API = "https://api.strategy.com"

URLS = [
    f"{API}/btc/timeSeries",
    f"{API}/btc/timeSeries?ticker=MSTR",
    f"{API}/btc/timeSeries?company=MSTR",
    f"{API}/btc/timeSeries?kpi=mNav",
    f"{API}/btc/timeSeries?metric=mNav",
    f"{API}/btc/getPreferreds",
    f"{API}/v2/btc/credit",
    f"{API}/btc/optionsData",
]


def fetch(url: str) -> bytes | None:
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
        with urllib.request.urlopen(req, timeout=60, context=CTX) as resp:
            body = resp.read()
            print(f"OK {resp.status} {len(body):8d} {url}")
            return body
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {url} {exc}")
        return None


if __name__ == "__main__":
    charts = fetch("https://www.strategy.com/_next/static/chunks/pages/charts-1a81ef1b5879238b.js")
    if charts:
        (CACHE / "charts.js").write_bytes(charts)
        text = charts.decode("utf-8", errors="replace")
        for key in ("timeSeries", "mNav", "download", "csv", "kpi"):
            idx = 0
            n = 0
            while n < 3:
                i = text.find(key, idx)
                if i < 0:
                    break
                print("\nCTX", key, text[max(0, i - 120) : i + 180].replace("\n", " "))
                idx = i + len(key)
                n += 1

    data_js = fetch("https://www.strategy.com/_next/static/chunks/pages/data-aca5aae91453e641.js")
    if data_js:
        (CACHE / "data_page.js").write_bytes(data_js)
        text = data_js.decode("utf-8", errors="replace")
        print("\n=== data page snippets ===")
        for key in ("timeSeries", "download", "csv", "kpiData", "api.strategy"):
            i = text.find(key)
            if i >= 0:
                print(key, text[max(0, i - 100) : i + 200].replace("\n", " "))

    for url in URLS:
        body = fetch(url)
        if not body:
            continue
        name = "ts_" + urllib.parse.quote(url.split("/btc/")[-1], safe="")[:80]
        (CACHE / name).write_bytes(body[:3_000_000])
        try:
            data = json.loads(body)
        except Exception:
            print("  not json", body[:200])
            continue
        if isinstance(data, dict):
            print("  keys", list(data.keys())[:40])
            for k, v in list(data.items())[:8]:
                if isinstance(v, list):
                    print(f"  {k} list {len(v)} first", str(v[0])[:180] if v else None)
                elif isinstance(v, dict):
                    print(f"  {k} dict keys", list(v.keys())[:20])
                else:
                    print(f"  {k}", str(v)[:120])
        elif isinstance(data, list):
            print("  list", len(data), "first", str(data[0])[:250] if data else None)
