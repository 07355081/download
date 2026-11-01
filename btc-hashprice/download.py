#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BTC 哈希价格（USD/PH/s/日）日序列 + 手续费占区块奖励。

- 哈希价格：Firsthand Bitcoin 免费日序列（hpUsd），非 Luxor Hashrate Index 专有 API
- 手续费占比：本站 mining_shutdown_price.json 的 feeInRewardPct（mempool）

用法：
  python download.py
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "output" / "json" / "btc_hashprice.json"

HP_URL = "https://firsthandbitcoin.com/data/api/series/hpUsd.json"
FEE_CANDIDATES = [
    ROOT / "mining-shutdown-price" / "output" / "json" / "mining_shutdown_price.json",
    Path("/root/dashboard/public/json/mining-shutdown-price/mining_shutdown_price.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "mining-shutdown-price" / "mining_shutdown_price.json",
]
FEE_URL = "https://data.wublock123.com/json/mining-shutdown-price/mining_shutdown_price.json"
UA = "dashboard-data/btc-hashprice"

def http_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_hashprice() -> list[dict[str, Any]]:
    doc = http_json(HP_URL)
    points = None
    if isinstance(doc, dict):
        # Firsthand: data=[] 为序列；points 常为条数 int
        raw = doc.get("data")
        if isinstance(raw, list):
            points = raw
        elif isinstance(doc.get("points"), list):
            points = doc["points"]
    if not isinstance(points, list):
        raise RuntimeError("hpUsd payload missing data[]")
    rows: list[dict[str, Any]] = []
    for item in points:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        day = str(item[0])[:10]
        try:
            value = float(item[1])
        except (TypeError, ValueError):
            continue
        if len(day) != 10 or value <= 0:
            continue
        rows.append({"day": day, "hashprice_usd": round(value, 4)})
    rows.sort(key=lambda x: x["day"])
    # dedupe keep last
    by_day = {row["day"]: row for row in rows}
    return [by_day[day] for day in sorted(by_day)]


def parse_fee_doc(doc: Any) -> list[tuple[str, float]]:
    data = doc.get("data") if isinstance(doc, dict) else doc
    out: list[tuple[str, float]] = []
    for row in data or []:
        if not isinstance(row, dict):
            continue
        day = str(row.get("date") or "")[:10]
        fee = row.get("feeInRewardPct")
        if len(day) != 10 or not isinstance(fee, (int, float)):
            continue
        out.append((day, float(fee)))
    out.sort()
    return out


def load_fee_series() -> list[tuple[str, float]]:
    """优先取末日最新的一份（本地落盘可能滞后于线上）。"""
    candidates: list[tuple[str, list[tuple[str, float]]]] = []
    for path in FEE_CANDIDATES:
        if not path.is_file():
            continue
        try:
            series = parse_fee_doc(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
        if series:
            candidates.append((str(path), series))
    try:
        series = parse_fee_doc(http_json(FEE_URL))
        if series:
            candidates.append((FEE_URL, series))
    except Exception as exc:
        print(f"[btc-hashprice] fee url skip: {exc}")
    if not candidates:
        return []
    label, best = max(candidates, key=lambda item: item[1][-1][0])
    print(f"[btc-hashprice] fee ← {label} last={best[-1][0]}")
    return best


def enrich(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        item = dict(row)
        if i >= 7:
            prev = rows[i - 7]["hashprice_usd"]
            if prev > 0:
                item["wow_pct"] = round((row["hashprice_usd"] / prev - 1.0) * 100.0, 2)
                item["wow_from"] = rows[i - 7]["day"]
                item["wow_from_usd"] = rows[i - 7]["hashprice_usd"]
        window = rows[max(0, i - 29) : i + 1]
        high = max(window, key=lambda x: x["hashprice_usd"])
        item["high_30d"] = high["hashprice_usd"]
        item["high_30d_day"] = high["day"]
        if high["hashprice_usd"] > 0:
            item["vs_30d_high_pct"] = round(
                (row["hashprice_usd"] / high["hashprice_usd"] - 1.0) * 100.0, 2
            )
        out.append(item)
    return out


def main() -> int:
    rows = enrich(load_hashprice())
    if len(rows) < 30:
        raise RuntimeError(f"hashprice series too short: {len(rows)}")
    fee_rows = load_fee_series()
    latest = dict(rows[-1])
    fee_by = {day: pct for day, pct in fee_rows}
    fee = fee_by.get(latest["day"])
    fee_day = latest["day"]
    if fee is None:
        earlier = [(d, v) for d, v in fee_rows if d <= latest["day"]]
        if earlier:
            fee_day, fee = earlier[-1]
    if fee is not None:
        latest["fee_share_pct"] = round(fee, 4)
        latest["fee_day"] = fee_day

    # May 14 calendar high in current year window (for article-style context)
    year = latest["day"][:4]
    may = [r for r in rows if r["day"].startswith(f"{year}-05-14")]
    if may:
        latest["ref_may14_usd"] = may[-1]["hashprice_usd"]
        latest["ref_may14_day"] = may[-1]["day"]
        if may[-1]["hashprice_usd"] > 0:
            latest["vs_may14_pct"] = round(
                (latest["hashprice_usd"] / may[-1]["hashprice_usd"] - 1.0) * 100.0, 2
            )

    doc = {
        "name": "btc_hashprice",
        "source": "firsthandbitcoin hpUsd + mempool feeInRewardPct",
        "note": (
            "哈希价格为公开日序列 USD/PH/s/日（Firsthand Bitcoin），"
            "与 Luxor Hashrate Index 同主题但非同一指数；"
            "手续费占比为本站 mempool 口径（手续费÷手续费+区块补贴）。"
        ),
        "attribution": "https://firsthandbitcoin.com/data",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest": latest,
        "history": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT)
    print(
        f"wrote {OUT} day={latest['day']} hp={latest['hashprice_usd']} "
        f"wow={latest.get('wow_pct')} vs30d={latest.get('vs_30d_high_pct')} "
        f"fee={latest.get('fee_share_pct')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
