#!/usr/bin/env python3
from __future__ import annotations

import json
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

UA = {"User-Agent": "dashboard-data/probe"}


def get(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read())


def main() -> None:
    ov = get(
        "https://api.llama.fi/overview/dexs"
        "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyVolume"
    )
    protos = [p for p in ov["protocols"] if str(p.get("category") or "") == "Prediction Market"]
    cands = []
    for p in protos:
        t30 = float(p.get("total30d") or 0)
        t24 = float(p.get("total24h") or 0)
        if t30 >= 1e7 or t24 >= 1e6:
            cands.append(p)
    print("significant", len(cands))

    slugs = {str(p["slug"]) for p in cands}
    for s in ("kalshi", "polymarket-us", "polymarket-international", "polymarket"):
        slugs.add(s)

    by_day: dict[str, float] = defaultdict(float)
    by_day_proto: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for slug in sorted(slugs):
        try:
            summary = get(f"https://api.llama.fi/summary/dexs/{slug}?dataType=dailyVolume")
        except Exception as exc:  # noqa: BLE001
            print("skip", slug, exc)
            continue
        name = str(summary.get("displayName") or summary.get("name") or slug)
        if "polymarket" in slug.lower():
            name = "Polymarket"
        elif slug == "kalshi":
            name = "Kalshi"
        chart = summary.get("totalDataChart") or []
        for ts, val in chart:
            day = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
            amount = float(val or 0)
            by_day[day] += amount
            by_day_proto[day][name] += amount
        print("ok", slug, "points", len(chart), "->", name)

    q = 0.0
    proto_q: dict[str, float] = defaultdict(float)
    for day, value in by_day.items():
        if day.startswith(("2026-07", "2026-08", "2026-09")):
            q += value
            for name, amount in by_day_proto[day].items():
                proto_q[name] += amount
    print("Q3 total B", round(q / 1e9, 2))
    for name, amount in sorted(proto_q.items(), key=lambda kv: -kv[1])[:10]:
        share = round(100 * amount / q, 1) if q else 0
        print(f"  {name} {round(amount / 1e9, 2)}B share={share}%")


if __name__ == "__main__":
    main()
