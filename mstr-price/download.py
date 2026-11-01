# -*- coding: utf-8 -*-
"""Strategy（MSTR）Yahoo 现价。用于跌破 100 美元。

输出：output/json/mstr_price.json
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
OUT = HERE / "output" / "json" / "mstr_price.json"
UA = "dashboard-data/mstr-price"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/MSTR?range=2y&interval=1d"
LEVEL = 100.0
NY = ZoneInfo("America/New_York")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    doc = http_json(YAHOO)
    res = ((doc.get("chart") or {}).get("result") or [None])[0]
    if not isinstance(res, dict):
        raise RuntimeError("Yahoo MSTR 无 result")
    meta = res.get("meta") or {}
    price = float(meta.get("regularMarketPrice") or 0)
    if price <= 0:
        raise RuntimeError("Yahoo MSTR 无有效价")
    prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or 0)
    change_pct = float(meta.get("regularMarketChangePercent") or 0)
    if not change_pct and prev > 0:
        change_pct = (price / prev - 1) * 100
    day_low = float(meta.get("regularMarketDayLow") or 0) or None
    day_high = float(meta.get("regularMarketDayHigh") or 0) or None
    asof = int(meta.get("regularMarketTime") or 0)
    session = (
        datetime.fromtimestamp(asof, NY).date().isoformat()
        if asof
        else datetime.now(NY).date().isoformat()
    )

    ts = res.get("timestamp") or []
    closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    last_below = None
    for t, close in zip(ts, closes):
        if close is None:
            continue
        if float(close) < LEVEL:
            last_below = datetime.fromtimestamp(int(t), NY).date().isoformat()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": now_iso(),
        "source": "Yahoo Finance chart MSTR",
        "note": "正股 MSTR，不是 STRC。非外电。",
        "level": LEVEL,
        "ticker": "MSTR",
        "name": "Strategy",
        "price": round(price, 4),
        "prev_close": round(prev, 4) if prev else None,
        "change_pct": round(change_pct, 2),
        "day_low": round(day_low, 4) if day_low else None,
        "day_high": round(day_high, 4) if day_high else None,
        "session_date": session,
        "below_level": price < LEVEL,
        "touched_level": bool(day_low and day_low < LEVEL) or price < LEVEL,
        "last_close_below": last_below,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[mstr-price] px={price:.2f} chg={change_pct:+.2f}% "
        f"low={day_low} below={payload['below_level']} last_below={last_below} → {OUT}"
    )


if __name__ == "__main__":
    main()
