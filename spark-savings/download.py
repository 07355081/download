# -*- coding: utf-8 -*-
"""Spark 储蓄存款：官方数据站公开接口，不占 Dune。

https://spark.data.blockanalitica.com/v1/savings/vaults/aggregates/
https://spark.data.blockanalitica.com/v1/savings/vaults/historic/
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "output" / "json" / "spark_savings.json"
BASE = "https://spark.data.blockanalitica.com"
PAGE = "https://data.spark.finance/savings"


def get_json(path: str) -> dict:
    req = urllib.request.Request(
        BASE + path,
        headers={"User-Agent": "dashboard-data/spark-savings", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=40) as resp:
        payload = json.loads(resp.read().decode("utf-8", "replace"))
    if not isinstance(payload, dict) or not payload.get("success"):
        raise RuntimeError(f"{path} 返回异常")
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"{path} 没有数据")
    return payload


def num(value: object) -> float:
    return float(value) if isinstance(value, (int, float, str)) and str(value) else 0.0


def main() -> int:
    totals = get_json("/v1/savings/vaults/aggregates/")["data"]
    vaults = get_json("/v1/savings/vaults/historic/")["data"]
    by_date: dict[str, float] = {}
    for row in totals:
        if not isinstance(row, dict) or not isinstance(row.get("date"), str):
            continue
        by_date[row["date"]] = num(row.get("tvl_usd"))
    if not by_date:
        raise RuntimeError("没有合计序列")
    latest_day = max(by_date)
    total = by_date[latest_day]
    high = max(by_date.values())
    latest_vaults = []
    susds = 0.0
    for row in vaults:
        if not isinstance(row, dict) or row.get("date") != latest_day:
            continue
        symbol = str(row.get("symbol") or "")
        tvl = num(row.get("tvl_usd"))
        if symbol == "sUSDS":
            susds = tvl
        latest_vaults.append({"symbol": symbol, "tvl_usd": round(tvl, 2)})
    latest_vaults.sort(key=lambda item: -item["tvl_usd"])
    if total <= 0 or not latest_vaults:
        raise RuntimeError(f"{latest_day} 金库为空")
    by_symbol: dict[str, dict[str, float]] = {}
    for row in vaults:
        if not isinstance(row, dict) or not isinstance(row.get("date"), str):
            continue
        symbol = str(row.get("symbol") or "")
        if not symbol:
            continue
        by_symbol.setdefault(symbol, {})[row["date"]] = num(row.get("tvl_usd"))

    def points(mapping: dict[str, float]) -> list[dict[str, float | str]]:
        return [
            {"month": day, "usd": round(amount, 2)}
            for day, amount in sorted(mapping.items())
            if amount > 0
        ]

    series: list[dict] = [{"label": "合计", "labelEn": "Total", "months": points(by_date)}]
    for item in latest_vaults:
        if item["symbol"] != "sUSDS" and item["tvl_usd"] < total * 0.01:
            continue
        history = points(by_symbol.get(item["symbol"]) or {})
        if len(history) < 2:
            continue
        series.append({"label": item["symbol"], "labelEn": item["symbol"], "months": history})
    doc = {
        "source": BASE + "/v1/savings/vaults/",
        "source_page": PAGE,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest": {
            "date": latest_day,
            "total_usd": round(total, 2),
            "susds_usd": round(susds, 2),
            "susds_share": round(susds / total, 4) if total else 0,
            "is_high": total >= high,
            "vaults": latest_vaults,
        },
        "series": series,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT)
    print(
        f"wrote {OUT} day={latest_day} total={total / 1e8:.2f}亿 "
        f"sUSDS={susds / 1e8:.2f}亿 share={doc['latest']['susds_share']:.1%}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
