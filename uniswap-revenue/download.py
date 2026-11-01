#!/usr/bin/env python3
"""Uniswap 协议收入按链（DefiLlama dailyRevenue）→ 落盘。

重点跟踪 Robinhood Chain 占比，用于核对 Token Terminal「RH 成最大收入来源」类新闻。
口径：DefiLlama Uniswap protocol revenue（按链 breakdown 求和），不是 TT 原表，不是链 gas。

输出：
  output/json/uni_revenue_by_chain.json

用法：
  python download.py
  python download.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from calendar import monthrange
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
JSON_DIR = HERE / "output" / "json"
OUT_PATH = JSON_DIR / "uni_revenue_by_chain.json"

LLAMA_URL = "https://api.llama.fi/summary/fees/uniswap?dataType=dailyRevenue"
FOCUS_CHAIN = "Robinhood Chain"
ATTEMPTS = 4
TIMEOUT = 90
SOURCE_URL = "https://defillama.com/protocol/uniswap"


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get_json(url: str) -> dict:
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "dashboard-data/uniswap-revenue"}
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < ATTEMPTS:
                wait = min(2**attempt, 15)
                print(f"    [warn] attempt {attempt}/{ATTEMPTS}: {exc}; retry in {wait}s")
                time.sleep(wait)
    raise RuntimeError(f"GET {url} failed after {ATTEMPTS} attempts: {last}")


def chain_totals(breakdown: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    if not isinstance(breakdown, dict):
        return out
    for chain, versions in breakdown.items():
        if not isinstance(versions, dict):
            continue
        total = 0.0
        for value in versions.values():
            try:
                total += float(value or 0)
            except (TypeError, ValueError):
                continue
        if total > 0:
            out[str(chain)] = total
    return out


def build_payload(doc: dict) -> dict:
    chart = doc.get("totalDataChartBreakdown") or []
    daily: list[dict[str, Any]] = []
    month_chains: dict[str, dict[str, float]] = {}
    month_days: dict[str, set[str]] = {}

    for point in chart:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        ts, breakdown = point[0], point[1]
        if not isinstance(ts, (int, float)):
            continue
        day = datetime.fromtimestamp(int(ts), timezone.utc).date().isoformat()
        by_chain = chain_totals(breakdown)
        revenue = sum(by_chain.values())
        rh = float(by_chain.get(FOCUS_CHAIN) or 0)
        share = (rh / revenue * 100.0) if revenue > 0 else 0.0
        top1_chain = ""
        top1_usd = 0.0
        if by_chain:
            top1_chain, top1_usd = max(by_chain.items(), key=lambda item: item[1])
        daily.append(
            {
                "day": day,
                "revenue_usd": round(revenue, 2),
                "rh_revenue_usd": round(rh, 2),
                "rh_share_pct": round(share, 4),
                "top1_chain": top1_chain,
                "top1_revenue_usd": round(top1_usd, 2),
                "rh_is_top1": top1_chain == FOCUS_CHAIN,
            }
        )
        month = day[:7]
        acc = month_chains.setdefault(month, {})
        for chain, value in by_chain.items():
            acc[chain] = acc.get(chain, 0.0) + value
        month_days.setdefault(month, set()).add(day)

    daily.sort(key=lambda row: row["day"])

    latest_day = daily[-1]["day"] if daily else ""
    latest_m = latest_day[:7] if latest_day else ""
    months: list[dict[str, Any]] = []
    for month in sorted(month_chains):
        by_chain = month_chains[month]
        rev = sum(by_chain.values())
        rh = float(by_chain.get(FOCUS_CHAIN) or 0)
        share = (rh / rev * 100.0) if rev > 0 else 0.0
        top1_chain = ""
        top1_usd = 0.0
        if by_chain:
            top1_chain, top1_usd = max(by_chain.items(), key=lambda item: item[1])
        y, mo = int(month[:4]), int(month[5:7])
        last_dom = monthrange(y, mo)[1]
        complete = month < latest_m or (
            month == latest_m and latest_day.endswith(f"-{last_dom:02d}")
        )
        months.append(
            {
                "month": month,
                "revenue_usd": round(rev, 2),
                "rh_revenue_usd": round(rh, 2),
                "rh_share_pct": round(share, 4),
                "top1_chain": top1_chain,
                "top1_revenue_usd": round(top1_usd, 2),
                "rh_is_top1": top1_chain == FOCUS_CHAIN,
                "complete": complete,
                "days": len(month_days.get(month, ())),
            }
        )

    return {
        "code": "0",
        "msg": "success",
        "name": "uni_revenue_by_chain",
        "description": "Uniswap 协议收入按链（DefiLlama dailyRevenue；跟踪 Robinhood Chain 占比）",
        "source": "defillama",
        "source_url": SOURCE_URL,
        "focus_chain": FOCUS_CHAIN,
        "methodology": {
            "revenue": "DefiLlama Uniswap protocol dailyRevenue, summed across versions per chain.",
            "note": "Same ballpark as Token Terminal Uniswap revenue by chain; not the same table.",
        },
        "updated_at": now_utc_iso(),
        "row_count": len(daily),
        "columns": [
            "day",
            "revenue_usd",
            "rh_revenue_usd",
            "rh_share_pct",
            "top1_chain",
            "top1_revenue_usd",
            "rh_is_top1",
        ],
        "data": daily,
        "months": months,
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", default=str(OUT_PATH), help="输出 JSON 路径")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("[uniswap-revenue] DefiLlama Uniswap dailyRevenue by chain")
    if args.dry_run:
        print("DRY-RUN: 将请求 dailyRevenue breakdown，不写盘")
        return 0

    started = time.time()
    doc = http_get_json(LLAMA_URL)
    payload = build_payload(doc)
    dest = Path(args.out)
    write_json(dest, payload)

    complete = [m for m in payload["months"] if m["complete"]]
    newest = complete[-1] if complete else None
    tip = ""
    if newest:
        tip = (
            f" newest={newest['month']} rev={newest['revenue_usd']:,.0f} "
            f"rh={newest['rh_revenue_usd']:,.0f} share={newest['rh_share_pct']:.1f}% "
            f"top1={newest['rh_is_top1']}"
        )
    print(f"    rows={payload['row_count']} months={len(payload['months'])}{tip} {time.time() - started:.1f}s")
    print(f"json -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
