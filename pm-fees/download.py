# -*- coding: utf-8 -*-
"""预测市场协议费用（DefiLlama dailyFees，免费）。

一次 overview/fees：按 category=Prediction Market 从 totalDataChartBreakdown
汇总日费用，滚成 UTC 自然月；拆出 Polymarket US / International / Combos。

输出：
  output/json/pm_fees.json

用法：
  python download.py
"""
from __future__ import annotations

import json
import time
import urllib.request
from calendar import monthrange
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "pm_fees.json"
API = "https://api.llama.fi/overview/fees?dataType=dailyFees"
UA = "dashboard-data/pm-fees"
CATEGORY = "Prediction Market"
POLY_NAMES = ("Polymarket US", "Polymarket International", "Polymarket Combos")
TIMEOUT = 180
ATTEMPTS = 3


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_json(url: str) -> Any:
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < ATTEMPTS:
                time.sleep(min(2**attempt, 12))
    raise RuntimeError(f"GET {url} failed: {last}")


def complete_months(
    by_m_total: dict[str, float],
    by_m_parts: dict[str, dict[str, float]],
    by_m_days: dict[str, set[str]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ym in sorted(by_m_total):
        y, m = int(ym[:4]), int(ym[5:7])
        need = monthrange(y, m)[1]
        if len(by_m_days[ym]) < need:
            continue
        parts = {k: round(v, 2) for k, v in by_m_parts[ym].items() if v > 0}
        poly = round(sum(parts.get(n, 0.0) for n in POLY_NAMES), 2)
        total = round(by_m_total[ym], 2)
        share = round(poly / total * 100.0, 2) if total > 0 else 0.0
        out.append(
            {
                "month": ym,
                "month_end": f"{ym}-{need:02d}",
                "fees_usd": total,
                "polymarket_usd": poly,
                "polymarket_us_usd": round(parts.get("Polymarket US", 0.0), 2),
                "polymarket_international_usd": round(parts.get("Polymarket International", 0.0), 2),
                "polymarket_combos_usd": round(parts.get("Polymarket Combos", 0.0), 2),
                "polymarket_share_pct": share,
                "parts": parts,
            }
        )
    return out


def main() -> int:
    doc = http_json(API)
    protocols = [
        p
        for p in (doc.get("protocols") or [])
        if isinstance(p, dict) and p.get("category") == CATEGORY
    ]
    names = {
        str(p.get("displayName") or p.get("name"))
        for p in protocols
        if (p.get("displayName") or p.get("name"))
    }
    if not names:
        raise SystemExit("Prediction Market 名单为空")

    by_m_total: dict[str, float] = defaultdict(float)
    by_m_parts: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    by_m_days: dict[str, set[str]] = defaultdict(set)
    for pt in doc.get("totalDataChartBreakdown") or []:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        try:
            day = datetime.fromtimestamp(int(pt[0]), tz=timezone.utc).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError):
            continue
        br = pt[1]
        if not isinstance(br, dict):
            continue
        ym = day[:7]
        by_m_days[ym].add(day)
        for name, raw in br.items():
            if name not in names:
                continue
            try:
                val = float(raw or 0)
            except (TypeError, ValueError):
                continue
            if val <= 0:
                continue
            by_m_total[ym] += val
            by_m_parts[ym][name] += val

    months = complete_months(by_m_total, by_m_parts, by_m_days)
    if len(months) < 2:
        raise SystemExit("完整月不足")

    latest = months[-1]
    series = [
        {
            "label": "Polymarket 费用",
            "labelEn": "Polymarket fees",
            "months": [{"month": m["month"], "usd": m["polymarket_usd"]} for m in months],
        },
        {
            "label": "预测市场赛道费用",
            "labelEn": "Prediction Market fees",
            "months": [{"month": m["month"], "usd": m["fees_usd"]} for m in months],
        },
        {
            "label": "Polymarket US",
            "labelEn": "Polymarket US",
            "months": [{"month": m["month"], "usd": m["polymarket_us_usd"]} for m in months],
        },
        {
            "label": "Polymarket International",
            "labelEn": "Polymarket International",
            "months": [
                {"month": m["month"], "usd": m["polymarket_international_usd"]} for m in months
            ],
        },
    ]
    payload = {
        "code": "0",
        "msg": "success",
        "name": "pm_fees",
        "description": "DefiLlama Prediction Market dailyFees，UTC 自然月",
        "source": "DefiLlama overview/fees dailyFees",
        "source_url": "https://defillama.com/fees",
        "updated_at": now_iso(),
        "category": CATEGORY,
        "protocol_count": len(names),
        "latest": {
            "month": latest["month"],
            "fees_usd": latest["fees_usd"],
            "polymarket_usd": latest["polymarket_usd"],
            "polymarket_us_usd": latest["polymarket_us_usd"],
            "polymarket_international_usd": latest["polymarket_international_usd"],
            "polymarket_share_pct": latest["polymarket_share_pct"],
        },
        "months": [
            {
                "month": m["month"],
                "month_end": m["month_end"],
                "fees_usd": m["fees_usd"],
                "polymarket_usd": m["polymarket_usd"],
                "polymarket_us_usd": m["polymarket_us_usd"],
                "polymarket_international_usd": m["polymarket_international_usd"],
                "polymarket_share_pct": m["polymarket_share_pct"],
                "top": [
                    {"name": n, "fees_usd": round(v, 2)}
                    for n, v in sorted(m["parts"].items(), key=lambda kv: -kv[1])[:8]
                ],
            }
            for m in months
        ],
        "series": series,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(
        f"wrote {OUT_PATH} months={len(months)} latest={latest['month']} "
        f"poly={latest['polymarket_usd']:.0f} share={latest['polymarket_share_pct']}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
