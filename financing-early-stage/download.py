#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""早期 vs 后期融资季度聚合（RootData fundraising_deals）。

早期：Pre-Seed / Seed / Seed Plus / Angel / Pre-A
后期：Series A 及以上（含 A1/B/B1/C…）、Post-IPO、M&A、Debt、OTC
非 CryptoRank。

输出：output/json/financing_early_stage.json
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "output" / "json" / "financing_early_stage.json"
DEAL_CANDIDATES = [
    ROOT / "financing-news" / "output" / "json" / "fundraising_deals.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "financing-news" / "fundraising_deals.json",
    Path("/root/dashboard/public/json/financing-news/fundraising_deals.json"),
]
EARLY = {"Pre-Seed", "Seed", "Seed Plus", "Angel", "Pre-A"}
LATE = {
    "Series A",
    "Series A1",
    "Series B",
    "Series B1",
    "Series C",
    "Series D",
    "Series E",
    "Series F",
    "Post-IPO",
    "M&A",
    "Debt Financing",
    "OTC",
}
BASE_Q = "2024Q2"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_deals() -> Path:
    for path in DEAL_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 fundraising_deals.json")


def load_rows(path: Path) -> list[dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = doc.get("data") if isinstance(doc, dict) else doc
    if isinstance(doc, dict) and not isinstance(rows, list):
        for value in doc.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                rows = value
                break
    if not isinstance(rows, list):
        raise RuntimeError("invalid fundraising_deals.json")
    return [row for row in rows if isinstance(row, dict)]


def parse_day(row: dict[str, Any]) -> date | None:
    raw = str(row.get("date") or "")[:10]
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def quarter_of(day: date) -> str:
    return f"{day.year}Q{(day.month - 1) // 3 + 1}"


def quarter_end(q: str) -> date:
    year = int(q[:4])
    qi = int(q[-1])
    month = qi * 3
    if month == 12:
        return date(year, 12, 31)
    return date.fromordinal(date(year, month + 1, 1).toordinal() - 1)


def amount(row: dict[str, Any]) -> float:
    raw = row.get("amount")
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return val if val > 0 else 0.0


def aggregate(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = defaultdict(
        lambda: {"early_n": 0, "early_usd": 0.0, "late_n": 0, "late_usd": 0.0}
    )
    for row in rows:
        rnd = str(row.get("round") or "").strip()
        day = parse_day(row)
        if not day:
            continue
        q = quarter_of(day)
        usd = amount(row)
        if rnd in EARLY:
            out[q]["early_n"] += 1
            out[q]["early_usd"] += usd
        elif rnd in LATE:
            out[q]["late_n"] += 1
            out[q]["late_usd"] += usd
    return dict(out)


def pct_change(cur: float, base: float) -> float | None:
    if base == 0:
        return None
    return (cur / base - 1.0) * 100.0


def main() -> int:
    path = resolve_deals()
    rows = load_rows(path)
    by_q = aggregate(rows)
    quarters = sorted(by_q)
    series = []
    for q in quarters:
        row = by_q[q]
        series.append(
            {
                "quarter": q,
                "early_n": int(row["early_n"]),
                "early_usd": round(row["early_usd"], 2),
                "late_n": int(row["late_n"]),
                "late_usd": round(row["late_usd"], 2),
            }
        )

    today = date.today()
    complete = [q for q in quarters if quarter_end(q) < today]
    latest = complete[-1] if complete else None
    base = by_q.get(BASE_Q)
    latest_row = by_q.get(latest) if latest else None
    vs_base = None
    if latest and latest_row and base:
        vs_base = {
            "base_quarter": BASE_Q,
            "early_n_pct": pct_change(latest_row["early_n"], base["early_n"]),
            "early_usd_pct": pct_change(latest_row["early_usd"], base["early_usd"]),
        }

    prev_q = complete[-2] if latest and len(complete) >= 2 else None
    qoq = None
    if latest and prev_q and latest_row:
        prev = by_q[prev_q]
        qoq = {
            "prev_quarter": prev_q,
            "early_n_pct": pct_change(latest_row["early_n"], prev["early_n"]),
            "early_usd_pct": pct_change(latest_row["early_usd"], prev["early_usd"]),
        }

    payload = {
        "updated_at": now_iso(),
        "source": "RootData via financing-news/fundraising_deals.json",
        "note": "早期=Pre-Seed/Seed/Seed Plus/Angel/Pre-A；后期=Series A+等。非 CryptoRank。",
        "early_rounds": sorted(EARLY),
        "late_rounds": sorted(LATE),
        "base_quarter": BASE_Q,
        "latest_complete_quarter": latest,
        "vs_base": vs_base,
        "qoq": qoq,
        "quarters": series,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT)
    print(f"wrote {OUT} latest={latest} quarters={len(series)} from={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
