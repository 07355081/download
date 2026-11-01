#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Solana 季度应用收入、Pump、季末 TVL、OnRe。

免费 DefiLlama，不占 Dune。不是 Messari REV，也不含整条 Solana RWA。
"""
from __future__ import annotations

import json
import time
import urllib.request
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_PATH = HERE / "output" / "json" / "solana_quarter.json"
TVL_CANDIDATES = [
    ROOT / "defi-tvl" / "output" / "json" / "defi_tvl.json",
    Path("/root/dashboard/public/json/defi-tvl/defi_tvl.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "defi-tvl" / "defi_tvl.json",
]
SOL_REV = (
    "https://api.llama.fi/overview/fees/solana"
    "?dataType=dailyRevenue"
)
PUMP_REV = "https://api.llama.fi/summary/fees/pump?dataType=dailyRevenue"
ONRE = "https://api.llama.fi/protocol/onre"
UA = "dashboard-data/solana-quarter"
TIMEOUT = 90


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get(url: str) -> Any:
    last: Exception | None = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(min(2**attempt, 8))
    raise RuntimeError(f"GET {url} failed: {last}")


def day_of(ts: int) -> str:
    if ts > 10_000_000_000:
        ts //= 1000
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def chart_by_day(doc: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for pt in doc.get("totalDataChart") or []:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        out[day_of(int(pt[0]))] = float(pt[1] or 0)
    return out


def as_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("total", "revenue", "value"):
            if isinstance(value.get(key), (int, float)):
                return float(value[key])
    return 0.0


def breakdown_totals(doc: dict, start: str, end: str) -> dict[str, float]:
    totals: dict[str, float] = {}
    for pt in doc.get("totalDataChartBreakdown") or []:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2 or not isinstance(pt[1], dict):
            continue
        day = day_of(int(pt[0]))
        if day < start or day > end:
            continue
        for name, raw in pt[1].items():
            totals[str(name)] = totals.get(str(name), 0.0) + as_float(raw)
    return totals


def onre_by_day(doc: dict) -> dict[str, float]:
    series = ((doc.get("chainTvls") or {}).get("Solana") or {}).get("tvl") or []
    out: dict[str, float] = {}
    for pt in series:
        if not isinstance(pt, dict) or pt.get("date") is None:
            continue
        out[day_of(int(pt["date"]))] = float(pt.get("totalLiquidityUSD") or 0)
    return out


def tvl_by_day() -> dict[str, float]:
    path = next((item for item in TVL_CANDIDATES if item.is_file()), None)
    if path is None:
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = doc.get("data") if isinstance(doc, dict) else []
    out: dict[str, float] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        day = str(row.get("time") or "")[:10]
        val = row.get("solana")
        if len(day) == 10 and isinstance(val, (int, float)):
            out[day] = float(val)
    print(f"[solana-quarter] tvl ← {path} n={len(out)}")
    return out


def quarter_bounds(year: int, quarter: int) -> tuple[str, str]:
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    start = date(year, start_month, 1).isoformat()
    end = date(year, end_month, monthrange(year, end_month)[1]).isoformat()
    return start, end


def sum_between(series: dict[str, float], start: str, end: str) -> tuple[float, int]:
    total = 0.0
    days = 0
    for day, val in series.items():
        if start <= day <= end:
            total += val
            days += 1
    return total, days


def level_on(series: dict[str, float], end: str) -> float | None:
    if not series:
        return None
    window = date.fromisoformat(end) - timedelta(days=3)
    best: tuple[str, float] | None = None
    for day, val in series.items():
        if window.isoformat() <= day <= end and (best is None or day > best[0]):
            best = (day, val)
    return None if best is None else best[1]


def is_pump(name: str) -> bool:
    text = name.lower().replace(" ", "").replace(".", "")
    return "pump" in text


def build() -> dict[str, Any]:
    sol = http_get(SOL_REV)
    pump = chart_by_day(http_get(PUMP_REV))
    chain = chart_by_day(sol)
    tvl = tvl_by_day()
    onre = onre_by_day(http_get(ONRE))
    today = datetime.now(timezone.utc).date()
    year, quarter = today.year, (today.month - 1) // 3 + 1
    quarters = []
    for _ in range(6):
        quarter -= 1
        if quarter == 0:
            year -= 1
            quarter = 4
        start, end = quarter_bounds(year, quarter)
        revenue, days = sum_between(chain, start, end)
        pump_usd, _pump_days = sum_between(pump, start, end)
        if days < 80 or revenue <= 0:
            continue
        ranked = breakdown_totals(sol, start, end)
        top_name = ""
        top_usd = 0.0
        if ranked:
            top_name, top_usd = max(ranked.items(), key=lambda item: item[1])
        quarters.append(
            {
                "id": f"{year}Q{quarter}",
                "start": start,
                "end": end,
                "app_revenue_usd": revenue,
                "pump_revenue_usd": pump_usd,
                "pump_share": pump_usd / revenue if revenue else None,
                "top_protocol": top_name,
                "top_protocol_usd": top_usd,
                "pump_is_top": bool(top_name) and is_pump(top_name),
                "revenue_days": days,
                "tvl_end_usd": level_on(tvl, end),
                "onre_end_usd": level_on(onre, end),
            }
        )
    quarters.sort(key=lambda item: item["end"])

    def series(label: str, label_en: str, key: str) -> dict[str, Any]:
        months = []
        for row in quarters:
            value = row.get(key)
            if isinstance(value, (int, float)) and value > 0:
                months.append({"month": row["id"], "usd": value})
        return {"label": label, "labelEn": label_en, "slug": key, "months": months}

    return {
        "generated_at": now_iso(),
        "source": "DefiLlama dailyRevenue / historicalChainTvl / protocol onre",
        "note": "应用收入不是 Messari REV。OnRe 不是整条 Solana RWA。",
        "quarters": quarters,
        "series": [
            series("Solana 应用收入", "Solana application revenue", "app_revenue_usd"),
            series("Pump.fun 收入", "Pump.fun revenue", "pump_revenue_usd"),
            series("Solana 季末 DeFi TVL", "Solana quarter-end DeFi TVL", "tvl_end_usd"),
            series("OnRe 季末 TVL", "OnRe quarter-end TVL", "onre_end_usd"),
        ],
    }


def main() -> int:
    payload = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[solana-quarter] wrote {OUT_PATH} quarters={len(payload['quarters'])}")
    if payload["quarters"]:
        last = payload["quarters"][-1]
        print(
            f"[solana-quarter] latest={last['id']} revenue={last['app_revenue_usd']:.0f} "
            f"pump={last['pump_revenue_usd']:.0f} top={last['top_protocol']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
