#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""美现货 BTC/ETH ETF 半年度净流（CoinGlass 日序列汇总）。

读看板已有 etf-flow-history，不新拉 API。
H1=1/1–6/30，H2=7/1–12/31；只产出已收完半年度。
含：半年度净流、是否序列内首次半年度净流出、该半内最长连续流出交易日、IBIT 半年度净流。

用法：
  python download.py
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "output" / "json" / "etf_halfyear.json"
FLOW_ROOTS = [
    Path("/root/dashboard/public/json/etf-flow-history"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "etf-flow-history",
    ROOT / "coinglass-history" / "etf-flow-history" / "output" / "json",
]


def load_flow(name: str) -> list[dict[str, Any]]:
    for base in FLOW_ROOTS:
        path = base / name
        if not path.is_file():
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        data = raw.get("data") if isinstance(raw, dict) else raw
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
    raise FileNotFoundError(name)


def day_of(row: dict[str, Any]) -> date | None:
    raw = row.get("time")
    if isinstance(raw, (int, float)):
        ts = int(raw)
        if ts > 1e12:
            ts //= 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).date()
    text = str(raw or "")[:10]
    if len(text) >= 10:
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    return None


def flow_of(row: dict[str, Any]) -> float | None:
    val = row.get("flow_usd")
    if isinstance(val, (int, float)):
        return float(val)
    return None


def half_bounds(year: int, half: int) -> tuple[date, date]:
    if half == 1:
        return date(year, 1, 1), date(year, 6, 30)
    return date(year, 7, 1), date(year, 12, 31)


def half_label(year: int, half: int) -> str:
    return f"{year}H{half}"


def complete_halves_through(today: date) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for year in range(2024, today.year + 1):
        for half in (1, 2):
            _, end = half_bounds(year, half)
            if end < today:
                out.append((year, half))
    return out


def series_days(rows: list[dict[str, Any]]) -> list[tuple[date, float]]:
    by: dict[date, float] = {}
    for row in rows:
        day = day_of(row)
        flow = flow_of(row)
        if day is None or flow is None:
            continue
        by[day] = flow
    return sorted(by.items())


def sum_half(days: list[tuple[date, float]], year: int, half: int) -> tuple[float, int]:
    start, end = half_bounds(year, half)
    total = 0.0
    n = 0
    for day, flow in days:
        if start <= day <= end:
            total += flow
            n += 1
    return total, n


def longest_outflow(
    days: list[tuple[date, float]], year: int, half: int
) -> dict[str, Any] | None:
    start, end = half_bounds(year, half)
    best = 0
    best_start: date | None = None
    best_end: date | None = None
    cur = 0
    cur_start: date | None = None
    for day, flow in days:
        if day < start or day > end:
            continue
        if flow < 0:
            if cur == 0:
                cur_start = day
            cur += 1
            if cur > best:
                best = cur
                best_start = cur_start
                best_end = day
        else:
            cur = 0
            cur_start = None
    if best <= 0 or best_start is None or best_end is None:
        return None
    return {
        "days": best,
        "start": best_start.isoformat(),
        "end": best_end.isoformat(),
    }


def ibit_sum(rows: list[dict[str, Any]], year: int, half: int) -> float | None:
    start, end = half_bounds(year, half)
    total = 0.0
    n = 0
    for row in rows:
        day = day_of(row)
        if day is None or day < start or day > end:
            continue
        for item in row.get("etf_flows") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("etf_ticker") or "").upper() != "IBIT":
                continue
            val = item.get("flow_usd")
            if isinstance(val, (int, float)):
                total += float(val)
                n += 1
    return round(total, 2) if n else None


def build_asset(name: str, rows: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    days = series_days(rows)
    halves: list[dict[str, Any]] = []
    for year, half in complete_halves_through(today):
        total, n = sum_half(days, year, half)
        if n < 40:
            continue
        streak = longest_outflow(days, year, half)
        item: dict[str, Any] = {
            "id": half_label(year, half),
            "year": year,
            "half": half,
            "end": half_bounds(year, half)[1].isoformat(),
            "flow_usd": round(total, 2),
            "sessions": n,
            "outflow_streak": streak,
        }
        if name == "BTC":
            item["ibit_flow_usd"] = ibit_sum(rows, year, half)
        halves.append(item)
    # mark first half-year net outflow in this asset series
    seen_out = False
    for item in halves:
        is_out = float(item["flow_usd"]) < 0
        item["first_halfyear_outflow"] = bool(is_out and not seen_out)
        if is_out:
            seen_out = True
    # mark if this half's streak equals series max among completed halves
    max_streak = max(
        (int((h.get("outflow_streak") or {}).get("days") or 0) for h in halves),
        default=0,
    )
    for item in halves:
        streak = item.get("outflow_streak") or {}
        item["streak_is_series_max"] = bool(
            streak and int(streak.get("days") or 0) == max_streak and max_streak > 0
        )
    return halves


def main() -> int:
    today = datetime.now(timezone.utc).date()
    btc_rows = load_flow("bitcoin.json")
    eth_rows = load_flow("ethereum.json")
    btc = build_asset("BTC", btc_rows, today)
    eth = build_asset("ETH", eth_rows, today)
    latest_id = None
    if btc or eth:
        ids = sorted({*(h["id"] for h in btc), *(h["id"] for h in eth)})
        latest_id = ids[-1] if ids else None
    doc = {
        "name": "etf_halfyear",
        "source": "coinglass etf-flow-history",
        "note": (
            "美现货 BTC/ETH ETF 半年度净流，由 CoinGlass 日净流加总。"
            "H1=1/1–6/30，H2=7/1–12/31。IBIT 取日表 etf_flows。"
            "非 DWF Labs。"
        ),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest_complete_id": latest_id,
        "btc": btc,
        "eth": eth,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT)
    b = btc[-1] if btc else {}
    e = eth[-1] if eth else {}
    print(
        f"wrote {OUT} latest={latest_id} "
        f"btc={b.get('flow_usd')} eth={e.get('flow_usd')} "
        f"btc_streak={(b.get('outflow_streak') or {}).get('days')} "
        f"ibit={b.get('ibit_flow_usd')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
