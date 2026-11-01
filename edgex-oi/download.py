#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""edgeX 未平仓（DefiLlama overview/open-interest，免费）。

口径：breakdown 中名称含 edgeX 的协议（edgeX Perps + edgeX V2）按日加总，
避免父级与子协议双计。数字可能与外电单点不完全一致。

输出：
  output/json/edgex_oi.json
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "edgex_oi.json"

API = "https://api.llama.fi/overview/open-interest"
UA = "dashboard-data/edgex-oi"
TIMEOUT = 120
ATTEMPTS = 4
NAME_SUBSTR = "edgex"


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
                time.sleep(min(2**attempt, 16))
    raise RuntimeError(f"GET {url} failed: {last}")


def day_from_ts(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def is_edgex_name(name: str) -> bool:
    return NAME_SUBSTR in str(name or "").lower().replace(" ", "")


def build_series(breakdown: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in breakdown:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        ts, mapping = item[0], item[1]
        if not isinstance(mapping, dict):
            continue
        parts: dict[str, float] = {}
        total = 0.0
        for name, value in mapping.items():
            if not is_edgex_name(str(name)):
                continue
            if not isinstance(value, (int, float)) or float(value) <= 0:
                continue
            amount = float(value)
            parts[str(name)] = amount
            total += amount
        if total <= 0:
            continue
        rows.append(
            {
                "time": day_from_ts(int(ts)),
                "open_interest": round(total, 2),
                "parts": {k: round(v, 2) for k, v in sorted(parts.items(), key=lambda kv: -kv[1])},
            }
        )
    rows.sort(key=lambda r: str(r["time"]))
    # 同日保留最后一条（breakdown 一般一日一点）
    by_day: dict[str, dict[str, Any]] = {}
    for row in rows:
        by_day[str(row["time"])] = row
    return [by_day[k] for k in sorted(by_day)]


def main() -> None:
    print("[edgex-oi] fetch overview/open-interest …")
    doc = http_get_json(API)
    if not isinstance(doc, dict):
        raise RuntimeError("overview 返回非 dict")
    breakdown = doc.get("totalDataChartBreakdown")
    if not isinstance(breakdown, list) or not breakdown:
        raise RuntimeError("缺少 totalDataChartBreakdown")
    data = build_series(breakdown)
    if not data:
        raise RuntimeError("未找到 edgeX 相关 OI")
    latest = data[-1]
    peak = max(data, key=lambda r: float(r["open_interest"]))
    print(
        f"  days={len(data)} latest={latest['time']} "
        f"oi={latest['open_interest']/1e8:.2f}亿 parts={latest.get('parts')} "
        f"peak={peak['time']} {peak['open_interest']/1e8:.2f}亿"
    )
    payload = {
        "updated_at": now_iso(),
        "source": "defillama",
        "source_url": "https://defillama.com/protocol/edgex?openInterest=true",
        "metric": "openInterest",
        "note": (
            "DefiLlama overview/open-interest breakdown 中名称含 edgeX 的协议日合计"
            "（含 edgeX Perps、edgeX V2）；与外电单点可能不一致。"
        ),
        "latest": {
            "time": latest["time"],
            "open_interest": latest["open_interest"],
            "parts": latest.get("parts") or {},
        },
        "peak": {
            "time": peak["time"],
            "open_interest": peak["open_interest"],
        },
        "data": data,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(f"[edgex-oi] wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
