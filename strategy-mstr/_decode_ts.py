"""Decompress timeSeries gzip and fetch MSTR mNav history."""
from __future__ import annotations

import gzip
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


def fetch(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json,application/gzip,*/*",
            "Accept-Encoding": "gzip, deflate",
            "Referer": "https://www.strategy.com/",
            "Origin": "https://www.strategy.com",
        },
    )
    with urllib.request.urlopen(req, timeout=90, context=CTX) as resp:
        body = resp.read()
        enc = (resp.headers.get("content-encoding") or "").lower()
        print("status", resp.status, "len", len(body), "enc", enc, "ctype", resp.headers.get("content-type"), url)
        if enc == "gzip" or body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
            print("  decompressed", len(body))
        return body


def summarize(data: object) -> None:
    if isinstance(data, dict):
        print("dict keys", list(data.keys())[:40])
        for k, v in list(data.items())[:12]:
            if isinstance(v, list):
                print(f"  {k}: list[{len(v)}] first={str(v[0])[:160] if v else None}")
            elif isinstance(v, dict):
                print(f"  {k}: dict keys={list(v.keys())[:25]}")
            else:
                print(f"  {k}: {str(v)[:120]}")
    elif isinstance(data, list):
        print("list", len(data), "first", str(data[0])[:300] if data else None)
        if data and isinstance(data[0], dict):
            print(" item keys", list(data[0].keys())[:40])


if __name__ == "__main__":
    raw = CACHE / "ts_timeSeries"
    if raw.exists():
        blob = raw.read_bytes()
        if blob[:2] == b"\x1f\x8b":
            blob = gzip.decompress(blob)
            print("local decompressed", len(blob))
            data = json.loads(blob)
            summarize(data)
            (CACHE / "timeSeries_full.json").write_text(json.dumps(data)[:200], encoding="utf-8")
            # write a compact inspect file
            (CACHE / "timeSeries_inspect.json").write_text(
                json.dumps(data if not isinstance(data, dict) else {k: (list(v.keys())[:20] if isinstance(v, dict) else (v[:2] if isinstance(v, list) else v)) for k, v in list(data.items())[:30]}, default=str, indent=2)[:20000],
                encoding="utf-8",
            )

    qs = urllib.parse.urlencode(
        {
            "from": "2026-07-23",
            "to": "2026-08-16",
            "tickers": "MSTR",
            "metrics": "mNav",
        }
    )
    body = fetch(f"{API}/btc/timeSeries?{qs}")
    (CACHE / "mnav_range.json").write_bytes(body[:2_000_000])
    try:
        data = json.loads(body)
        summarize(data)
        (CACHE / "mnav_range_pretty.json").write_text(json.dumps(data, indent=2)[:400000], encoding="utf-8")
    except Exception as exc:
        print("parse fail", exc, body[:80])

    # data download proxy
    for url in (
        "https://www.strategy.com/api/download/btc/timeSeries?tickers=MSTR&metrics=mNav&from=2026-07-23&to=2026-08-16",
        "https://www.strategy.com/api/download?path=/btc/timeSeries&tickers=MSTR&metrics=mNav&from=2026-07-23&to=2026-08-16",
    ):
        try:
            b = fetch(url)
            print("download bytes", len(b), b[:60])
        except Exception as exc:
            print("FAIL", url, exc)
