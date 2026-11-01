# -*- coding: utf-8 -*-
"""Robinhood Chain vs Base 日交易笔数（GrowThePie 免费 fundamentals）。

输出: output/json/rh_base_txcount.json
不占 Dune / Token Terminal。
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT = HERE / "output" / "json" / "rh_base_txcount.json"
URL = "https://api.growthepie.xyz/v1/fundamentals.json"
ORIGINS = ("robinhood", "base")
METRIC = "txcount"
UA = "wublock-dashboard/rh-base-tx"


def fetch() -> list[dict[str, Any]]:
    req = urllib.request.Request(URL, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, list):
        raise RuntimeError("growthepie fundamentals 非 list")
    return data


def build_rows(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_day: dict[str, dict[str, float]] = {}
    for item in raw:
        if item.get("metric_key") != METRIC:
            continue
        origin = str(item.get("origin_key") or "")
        if origin not in ORIGINS:
            continue
        day = str(item.get("date") or "")[:10]
        if len(day) != 10:
            continue
        try:
            value = float(item.get("value"))
        except (TypeError, ValueError):
            continue
        by_day.setdefault(day, {})[origin] = value
    rows: list[dict[str, Any]] = []
    for day in sorted(by_day):
        row = by_day[day]
        if "robinhood" not in row or "base" not in row:
            continue
        rh = float(row["robinhood"])
        base = float(row["base"])
        rows.append(
            {
                "day": day,
                "robinhood_txns": rh,
                "base_txns": base,
                "rh_gt_base": rh > base,
            }
        )
    return rows


def main() -> int:
    raw = fetch()
    rows = build_rows(raw)
    if not rows:
        raise SystemExit("无 robinhood+base 对齐的 txcount 日")
    data_rows = [
        {
            **row,
            "value": round(row["robinhood_txns"] / row["base_txns"], 3),
        }
        for row in rows
        if row.get("base_txns")
    ]
    # lark-feeds 用 series[].months[].{month,usd} 画双线；单列 value 是 RH/Base 比值。
    series = [
        {
            "label": "Robinhood 交易笔数",
            "labelEn": "Robinhood tx count",
            "months": [{"month": r["day"], "usd": r["robinhood_txns"]} for r in data_rows],
        },
        {
            "label": "Base 交易笔数",
            "labelEn": "Base tx count",
            "months": [{"month": r["day"], "usd": r["base_txns"]} for r in data_rows],
        },
    ]
    payload = {
        "code": "0",
        "msg": "success",
        "source": "growthepie fundamentals txcount",
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data": data_rows,
        "series": series,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT)
    alias = OUT.parent / "rh_base_tx.json"
    alias.write_text(OUT.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"wrote {OUT} rows={len(rows)} latest={rows[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
