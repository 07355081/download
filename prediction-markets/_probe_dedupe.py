#!/usr/bin/env python3
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone

UA = {"User-Agent": "dashboard-data/probe"}


def get(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read())


def q3(chart: list) -> float:
    total = 0.0
    for ts, val in chart:
        day = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        if day.startswith(("2026-07", "2026-08", "2026-09")):
            total += float(val or 0)
    return total


def main() -> None:
    for slug in (
        "polymarket",
        "polymarket-us",
        "polymarket-international",
        "polymarket-combos",
        "kalshi",
    ):
        summary = get(f"https://api.llama.fi/summary/dexs/{slug}?dataType=dailyVolume")
        chart = summary.get("totalDataChart") or []
        print(slug, "name=", summary.get("name"), "q3B=", round(q3(chart) / 1e9, 2), "n=", len(chart))


if __name__ == "__main__":
    main()
