"""Dump Strategy API JSON and look for history endpoints in more page chunks."""
from __future__ import annotations

import json
import re
import ssl
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
CTX = ssl.create_default_context()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
BASE = "https://www.strategy.com"
API = "https://api.strategy.com"

MORE = [
    f"{API}/btc/kpiData",
    f"{API}/btc/mstrOptionsData",
    f"{API}/btc/strcKpiData",
    f"{API}/btc/strdKpiData",
    f"{API}/btc/strkKpiData",
    f"{API}/btc/strfKpiData",
    f"{API}/btc/debtKpiData",
    f"{API}/btc/creditKpiData",
    f"{API}/btc/assetsKpiData",
    f"{API}/btc/chartsData",
    f"{API}/btc/chartData",
    f"{API}/btc/metricHistory",
    f"{API}/btc/mstrMetricHistory",
    f"{API}/btc/historical",
    f"{API}/btc/mstrKpiDataHistory",
    f"{API}/btc/downloadMetrics",
    f"{API}/btc/csv",
    f"{API}/btc/mstrCsv",
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
        with urllib.request.urlopen(req, timeout=30, context=CTX) as resp:
            body = resp.read()
            print(f"OK {resp.status} {len(body):8d} {url}")
            return body
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {url} {exc}")
        return None


def pretty_print_json(name: str, body: bytes) -> None:
    data = json.loads(body)
    (CACHE / name).write_text(json.dumps(data, indent=2)[:500_000], encoding="utf-8")
    if isinstance(data, dict):
        print(" keys", list(data.keys())[:30])
        results = data.get("results")
        if isinstance(results, dict):
            print(" results keys", list(results.keys())[:50])
        elif isinstance(results, list) and results:
            print(" results[0] keys", list(results[0].keys()) if isinstance(results[0], dict) else type(results[0]))
    elif isinstance(data, list) and data:
        print(" list", len(data), "item keys", list(data[0].keys()) if isinstance(data[0], dict) else type(data[0]))


def download_page_js() -> None:
    man = (CACHE / "js___next_static_6a80fbe95a677e96f5ce1e7c__buildManifest.js").read_text(
        encoding="utf-8", errors="replace"
    )
    # pull /charts and /data chunk filenames
    for page in ("/charts", "/data", "/bitcoin", "/purchases", "/shares"):
        m = re.search(rf'"{re.escape(page)}":\[(.*?)\]', man)
        if not m:
            print("no manifest", page)
            continue
        chunks = re.findall(r'static/chunks/[^"\]]+', m.group(1))
        print(page, "chunks", chunks[-3:])
        for rel in chunks:
            if "pages/" not in rel and "8882" not in rel and "7892" not in rel:
                continue
            url = f"{BASE}/_next/{rel}"
            body = fetch(url)
            if not body:
                continue
            text = body.decode("utf-8", errors="replace")
            for m2 in re.finditer(r'api\.strategy\.com["\']?\s*,\s*["\'](/[^"\']+)["\']', text):
                print("  PATH", m2.group(1))
            for m2 in re.finditer(r'["\'](/btc/[^"\']+)["\']', text):
                print("  /btc", m2.group(1))


if __name__ == "__main__":
    for url in MORE:
        body = fetch(url)
        if body and (body[:1] in (b"{", b"[") or b"json" in body[:40].lower()):
            pretty_print_json("try_" + url.split("/")[-1] + ".json", body)

    print("\n=== known working ===")
    for name in ("bitcoinKpis", "mstrKpiData"):
        files = list(CACHE.glob(f"resp_*{name}"))
        for f in files:
            pretty_print_json(f"pretty_{name}.json", f.read_bytes())

    print("\n=== page js ===")
    download_page_js()
