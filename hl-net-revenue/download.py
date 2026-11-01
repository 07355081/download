#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hyperliquid 永续与 Ethereum / Solana / BNB Chain 近 30 日净收入。

DefiLlama summary/fees?dataType=dailyRevenue。
Hyperliquid 取永续，三条公链取链本身的净收入，不是各链应用收入加总。
丢掉尚未结束的 UTC 日。滚动 30 个日历日求和。

输出：
  output/json/hl_net_revenue.json
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "hl_net_revenue.json"

UA = "dashboard-data/hl-net-revenue"
TIMEOUT = 90
ATTEMPTS = 3
WINDOW = 30
KEEP = 420

SERIES = (
    ("hyperliquid-perps", "Hyperliquid 永续", "Hyperliquid perps"),
    ("ethereum", "Ethereum", "Ethereum"),
    ("solana", "Solana", "Solana"),
    ("bsc", "BNB Chain", "BNB Chain"),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get_json(url: str) -> Any:
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < ATTEMPTS:
                time.sleep(min(2**attempt, 12))
    raise RuntimeError(f"GET {url} failed: {last}")


def daily_points(doc: dict[str, Any]) -> list[tuple[datetime, float]]:
    today = datetime.now(timezone.utc).date()
    merged: dict[datetime, float] = {}
    for item in doc.get("totalDataChart") or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        ts, val = item[0], item[1]
        if not isinstance(ts, (int, float)) or not isinstance(val, (int, float)):
            continue
        day = datetime.fromtimestamp(int(ts), timezone.utc).date()
        if day >= today:
            continue
        merged[day] = merged.get(day, 0.0) + float(val)
    return sorted(merged.items())


def rolling(points: list[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
    if not points:
        return []
    days = [day for day, _ in points]
    values = [val for _, val in points]
    prefix = [0.0]
    for val in values:
        prefix.append(prefix[-1] + val)
    out: list[tuple[datetime, float]] = []
    left = 0
    for index, day in enumerate(days):
        start = day - timedelta(days=WINDOW - 1)
        while left < index and days[left] < start:
            left += 1
        if days[0] > start:
            continue
        total = prefix[index + 1] - prefix[left]
        out.append((day, total))
    return out[-KEEP:]


def fetch_one(slug: str) -> list[tuple[datetime, float]]:
    url = (
        "https://api.llama.fi/summary/fees/"
        f"{slug}?dataType=dailyRevenue&excludeTotalDataChartBreakdown=true"
    )
    print(f"[hl-net-revenue] {slug}")
    doc = http_get_json(url)
    if not isinstance(doc, dict):
        raise RuntimeError(f"{slug} response is not an object")
    points = rolling(daily_points(doc))
    if len(points) < 2:
        raise RuntimeError(f"{slug} rolling series too short")
    return points


def main() -> None:
    series_points = [(slug, label, label_en, fetch_one(slug)) for slug, label, label_en in SERIES]
    common = min(points[-1][0] for _, _, _, points in series_points)
    by_slug: dict[str, dict[datetime, float]] = {}
    series: list[dict[str, Any]] = []
    for slug, label, label_en, points in series_points:
        kept = [(day, val) for day, val in points if day <= common]
        if len(kept) < 2 or kept[-1][0] != common:
            raise RuntimeError(f"{slug} missing common day {common.isoformat()}")
        by_slug[slug] = {day: val for day, val in kept}
        series.append(
            {
                "slug": slug,
                "label": label,
                "labelEn": label_en,
                "months": [{"month": day.isoformat(), "usd": round(val, 2)} for day, val in kept],
            }
        )
    eth = by_slug["ethereum"][common]
    rows = []
    for slug, label, label_en, _points in series_points:
        usd = by_slug[slug][common]
        row: dict[str, Any] = {
            "slug": slug,
            "name": label_en,
            "name_zh": label,
            "usd": round(usd, 2),
        }
        if slug == "hyperliquid-perps" and eth > 0:
            row["vs_ethereum"] = round(usd / eth, 2)
        rows.append(row)
    doc = {
        "updatedAt": now_iso(),
        "source": "defillama/summary/fees dailyRevenue",
        "window_days": WINDOW,
        "day": common.isoformat(),
        "latest": {"day": common.isoformat(), "rows": rows},
        "series": series,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    hl = by_slug["hyperliquid-perps"][common]
    print(
        f"[hl-net-revenue] day={common.isoformat()} "
        f"hl={hl/1e6:.2f}m eth={eth/1e6:.2f}m "
        f"sol={by_slug['solana'][common]/1e6:.2f}m bnb={by_slug['bsc'][common]/1e6:.2f}m "
        f"-> {OUT_PATH}"
    )


if __name__ == "__main__":
    main()
