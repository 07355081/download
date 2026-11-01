#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hyperliquid HIP-3 可交易永续市场数（官方 info，免费）。

allPerpMetas + perpDexs：跳过 Core（下标 0），其余 builder 市场合计。
不是全行业 Perp DEX，也不是 Blockworks 的 178。

输出：output/json/hl_hip3_markets.json
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "hl_hip3_markets.json"
API = "https://api.hyperliquid.xyz/info"
UA = "dashboard-data/hl-hip3-markets"
TIMEOUT = 40
ATTEMPTS = 5


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def post_json(payload: dict[str, Any]) -> Any:
    last: Exception | None = None
    body = json.dumps(payload).encode("utf-8")
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(
                API,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": UA,
                    "Accept": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            wait = min(2**attempt, 30)
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
                wait = max(wait, 12 * attempt)
            print(f"  [warn] attempt {attempt}/{ATTEMPTS}: {exc}; sleep {wait}s")
            if attempt < ATTEMPTS:
                time.sleep(wait)
    raise RuntimeError(f"POST {API} failed: {last}")


def load_days() -> list[dict[str, Any]]:
    if not OUT_PATH.is_file():
        return []
    try:
        doc = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    data = doc.get("data") if isinstance(doc, dict) else None
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def main() -> int:
    print("[hl-hip3-markets] fetch perpDexs + allPerpMetas …")
    dexs = post_json({"type": "perpDexs"})
    time.sleep(2)
    metas = post_json({"type": "allPerpMetas"})
    if not isinstance(dexs, list) or not isinstance(metas, list):
        raise RuntimeError("unexpected response shape")
    if len(metas) < 2:
        raise RuntimeError("no HIP-3 metas")

    markets: list[dict[str, str]] = []
    for index, meta in enumerate(metas):
        if index == 0:
            continue
        if not isinstance(meta, dict):
            continue
        dex_row = dexs[index] if index < len(dexs) else None
        builder = ""
        if isinstance(dex_row, dict):
            builder = str(dex_row.get("name") or dex_row.get("fullName") or "")
        universe = meta.get("universe") or []
        if not isinstance(universe, list):
            continue
        for item in universe:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            base = name.split(":", 1)[-1]
            if not builder and ":" in name:
                builder = name.split(":", 1)[0]
            markets.append({"builder": builder or "unknown", "market": name, "underlying": base})

    if not markets:
        raise RuntimeError("HIP-3 market list empty")

    markets.sort(key=lambda row: (row["builder"], row["market"]))
    by_builder = Counter(row["builder"] for row in markets)
    unique = sorted({row["underlying"] for row in markets})
    fingerprint = "|".join(row["market"] for row in markets)
    day = utc_day()
    builders = [
        {"name": name, "markets": count}
        for name, count in sorted(by_builder.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    history = [row for row in load_days() if str(row.get("day")) != day]
    history.append(
        {
            "day": day,
            "series_count": len(markets),
            "unique_count": len(unique),
            "builders": len(builders),
        }
    )
    history.sort(key=lambda row: str(row.get("day") or ""))
    if len(history) == 1:
        only = history[0]
        try:
            prev = (
                datetime.strptime(str(only.get("day")), "%Y-%m-%d").date() - timedelta(days=1)
            ).isoformat()
        except ValueError:
            prev = ""
        if prev:
            history.insert(
                0,
                {
                    "day": prev,
                    "series_count": only.get("series_count"),
                    "unique_count": only.get("unique_count"),
                    "builders": only.get("builders"),
                },
            )
    history = history[-400:]
    payload = {
        "updated_at": now_iso(),
        "source": "hyperliquid/info",
        "source_url": "https://hyperliquid.xyz",
        "note": "Hyperliquid HIP-3（非 Core）永续市场数。不是全行业 Perp DEX，也不是 Blockworks 标的统计。",
        "latest": {
            "day": day,
            "series_count": len(markets),
            "unique_count": len(unique),
            "fingerprint": fingerprint,
            "builders": builders,
            "top_builders": builders[:5],
            "markets": markets,
        },
        "data": history,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    top = "、".join(f"{row['name']} {row['markets']}" for row in builders[:4])
    print(
        f"[hl-hip3-markets] day={day} markets={len(markets)} "
        f"unique={len(unique)} builders={len(builders)} top=[{top}]"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
