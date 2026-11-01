#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预测市场近 7 日名义成交与未平仓（DefiLlama 免费概览）。

成交：overview/dexs category=Prediction Market 的 total7d。
持仓：overview/open-interest 同分类。
Polymarket 子协议合并。跳过父级汇总，避免双计。没有 Rothera。

输出：
  output/json/pm_weekly.json
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "pm_weekly.json"

DEX_URL = (
    "https://api.llama.fi/overview/dexs"
    "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyVolume"
)
OI_URL = "https://api.llama.fi/overview/open-interest"
UA = "dashboard-data/pm-weekly"
TIMEOUT = 90
ATTEMPTS = 3
CATEGORY = "Prediction Market"

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


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


def brand_of(slug: str, name: str) -> str:
    blob = f"{slug} {name}".lower()
    if "polymarket" in blob:
        return "Polymarket"
    if "kalshi" in blob:
        return "Kalshi"
    if "sx-bet" in blob or "sx bet" in blob:
        return "SX Bet"
    return name or slug


def selected(protocols: list[Any]) -> list[dict[str, Any]]:
    rows = [
        p
        for p in protocols
        if isinstance(p, dict)
        and str(p.get("category") or "") == CATEGORY
        and p.get("slug")
    ]
    parent_slugs = set()
    for row in rows:
        parent = str(row.get("parentProtocol") or "")
        if parent.startswith("parent#"):
            parent_slugs.add(parent[len("parent#") :])
    return [row for row in rows if str(row["slug"]) not in parent_slugs]


def num(row: dict[str, Any], keys: tuple[str, ...]) -> float:
    for key in keys:
        value = row.get(key)
        if isinstance(value, (int, float)) and float(value) > 0:
            return float(value)
    return 0.0


def rollup(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    totals: dict[str, float] = {}
    for row in rows:
        amount = num(row, keys)
        if amount <= 0:
            continue
        name = brand_of(str(row.get("slug") or ""), str(row.get("displayName") or row.get("name") or ""))
        totals[name] = totals.get(name, 0.0) + amount
    return [
        {"name": name, "usd": round(amount, 2)}
        for name, amount in sorted(totals.items(), key=lambda kv: -kv[1])
    ]


def load_days() -> list[dict[str, Any]]:
    if not OUT_PATH.is_file():
        return []
    try:
        doc = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    data = doc.get("data") if isinstance(doc, dict) else None
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def main() -> None:
    print("[pm-weekly] fetch dex volume + open interest …")
    dex = http_get_json(DEX_URL)
    oi = http_get_json(OI_URL)
    vol_rows = selected(dex.get("protocols") or [])
    oi_rows = selected(oi.get("protocols") or [])
    volume = rollup(vol_rows, ("total7d",))
    interest = rollup(oi_rows, ("total24h", "totalOpenInterest", "openInterest"))
    if not volume and not interest:
        raise RuntimeError("预测市场成交与持仓都为空")
    day = utc_day()
    snap = {
        "time": day,
        "volume_7d": round(sum(row["usd"] for row in volume), 2),
        "open_interest": round(sum(row["usd"] for row in interest), 2),
        "volume": volume[:8],
        "oi": interest[:8],
    }
    data = [row for row in load_days() if str(row.get("time")) != day]
    data.append(snap)
    data.sort(key=lambda row: str(row.get("time") or ""))
    payload = {
        "updated_at": now_iso(),
        "source": "defillama",
        "note": "DefiLlama 预测市场近7日名义成交与未平仓。Polymarket 已合并。无 Rothera。不是 Artemis。",
        "latest": snap,
        "data": data,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(
        f"[pm-weekly] {day} vol7d={snap['volume_7d']/1e8:.2f}亿 "
        f"oi={snap['open_interest']/1e8:.2f}亿 "
        f"top_vol={volume[:3]} top_oi={interest[:3]}"
    )


if __name__ == "__main__":
    main()
