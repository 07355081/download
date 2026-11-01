# -*- coding: utf-8 -*-
"""全市场协议费用 / 收入日序列（DefiLlama overview/fees，免费，不占 Dune）。

口径：
  - dailyFees / dailyRevenue 的 totalDataChart（全球合计）
  - 与 Lookonchain「协议收入」可能覆盖集不同，发稿须写 DefiLlama

输出：
  output/json/protocol_revenue_daily.json

用法：
  python download.py
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from calendar import monthrange
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
JSON_DIR = HERE / "output" / "json"
OUT_PATH = JSON_DIR / "protocol_revenue_daily.json"

API = "https://api.llama.fi"
UA = "dashboard-data/protocol-revenue-daily"
TIMEOUT = 120
ATTEMPTS = 3

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get_json(url: str) -> Any:
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < ATTEMPTS:
                time.sleep(min(2**attempt, 12))
    raise RuntimeError(f"GET {url} failed: {last}")


def chart_to_map(chart: list[Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for pt in chart or []:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        ts, val = pt[0], pt[1]
        if not isinstance(ts, (int, float)) or not isinstance(val, (int, float)):
            continue
        day = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        out[day] = float(val)
    return out


def fetch_metric(data_type: str) -> dict[str, float]:
    url = (
        f"{API}/overview/fees"
        f"?excludeTotalDataChart=false&excludeTotalDataChartBreakdown=true"
        f"&dataType={data_type}"
    )
    payload = http_get_json(url)
    if not isinstance(payload, dict):
        return {}
    return chart_to_map(payload.get("totalDataChart") or [])


def pct_change(curr: float, prev: float) -> float | None:
    if prev == 0:
        return None
    return (curr / prev - 1.0) * 100.0


def week_ending_sunday(day: str) -> str:
    """UTC 日所属自然周：以该周周日为周标签（Lookonchain 常见周报窗）。"""
    d = datetime.strptime(day, "%Y-%m-%d").date()
    # Monday=0 … Sunday=6 → 推到周日
    sun = d + timedelta(days=(6 - d.weekday()))
    return sun.isoformat()


def build_weeks(day_map: dict[str, float]) -> list[dict[str, Any]]:
    by_w: dict[str, float] = defaultdict(float)
    days_in_w: dict[str, set[str]] = defaultdict(set)
    for day, val in day_map.items():
        w = week_ending_sunday(day)
        by_w[w] += val
        days_in_w[w].add(day)
    rows: list[dict[str, Any]] = []
    prev: float | None = None
    for w in sorted(by_w):
        # 至少 5 个交易日才算完整周（周末常有数据，DefiLlama 日更）
        if len(days_in_w[w]) < 5:
            continue
        total = by_w[w]
        wow = pct_change(total, prev) if prev is not None else None
        rows.append(
            {
                "week_end": w,
                "value_usd": round(total, 2),
                "days": len(days_in_w[w]),
                "wow_pct": None if wow is None else round(wow, 4),
            }
        )
        prev = total
    return rows


def complete_months(day_map: dict[str, float]) -> list[dict[str, Any]]:
    by_m: dict[str, float] = defaultdict(float)
    days_in_m: dict[str, set[str]] = defaultdict(set)
    for day, val in day_map.items():
        ym = day[:7]
        by_m[ym] += val
        days_in_m[ym].add(day)
    rows: list[dict[str, Any]] = []
    prev: float | None = None
    for ym in sorted(by_m):
        y, m = int(ym[:4]), int(ym[5:7])
        need = monthrange(y, m)[1]
        if len(days_in_m[ym]) < need:
            continue
        total = by_m[ym]
        mom = pct_change(total, prev) if prev is not None else None
        rows.append(
            {
                "month": ym,
                "value_usd": round(total, 2),
                "mom_pct": None if mom is None else round(mom, 4),
            }
        )
        prev = total
    return rows


def main() -> int:
    print("[protocol-revenue-daily] fetch dailyFees…")
    fees = fetch_metric("dailyFees")
    time.sleep(0.5)
    print("[protocol-revenue-daily] fetch dailyRevenue…")
    revenue = fetch_metric("dailyRevenue")

    if not fees and not revenue:
        print("[ERROR] empty charts", flush=True)
        return 1

    all_days = sorted(set(fees) | set(revenue))

    data: list[dict[str, Any]] = []
    for day in all_days:
        row: dict[str, Any] = {"time": day}
        if day in fees:
            row["fees_usd"] = fees[day]
        if day in revenue:
            row["revenue_usd"] = revenue[day]
        if len(row) > 1:
            data.append(row)

    fee_weeks = build_weeks(fees)
    rev_weeks = build_weeks(revenue)
    fee_months = complete_months(fees)
    rev_months = complete_months(revenue)

    latest = data[-1] if data else {}
    payload = {
        "code": "0",
        "msg": "success",
        "name": "protocol_revenue_daily",
        "description": "全市场协议日费用/收入合计（DefiLlama overview/fees）",
        "source": "defillama",
        "source_url": "https://defillama.com/fees",
        "metrics": ["dailyFees", "dailyRevenue"],
        "note": (
            "DefiLlama 全协议 fees/revenue 口径；与 Lookonchain 周报「协议收入」覆盖集可能不同，"
            "发稿须写 DefiLlama，勿标 Lookonchain 同源。周标签为 UTC 自然周周日。"
        ),
        "updated_at": now_iso(),
        "row_count": len(data),
        "latest_day": latest.get("time"),
        "latest_fees_usd": latest.get("fees_usd"),
        "latest_revenue_usd": latest.get("revenue_usd"),
        "weeks_fees": fee_weeks[-52:],
        "weeks_revenue": rev_weeks[-52:],
        "months_fees": fee_months[-36:],
        "months_revenue": rev_months[-36:],
        "data": data,
    }

    JSON_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(OUT_PATH)

    last_rev = rev_weeks[-1] if rev_weeks else None
    print(
        f"[protocol-revenue-daily] wrote {OUT_PATH} rows={len(data)} "
        f"latest={latest.get('time')} fees={latest.get('fees_usd')} "
        f"revenue={latest.get('revenue_usd')} "
        f"last_rev_week={None if not last_rev else last_rev}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
