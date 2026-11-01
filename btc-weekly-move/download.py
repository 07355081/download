# -*- coding: utf-8 -*-
"""比特币单周涨跌（Yahoo BTC-USD 日线，免费）。

周定义：上周日收盘 → 本周日收盘（UTC 日期）。
与 Galaxy 的小时点可能差几十到几百美元，排名用这套自有序列，不标 Galaxy。

输出：
  output/json/btc_weekly_move.json
"""
from __future__ import annotations

import json
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "btc_weekly_move.json"
URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD"
    "?interval=1d&period1=1410825600&period2=2000000000"
)
UA = "dashboard-data/btc-weekly-move"
SINCE_PCT = date(2023, 3, 1)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_closes() -> dict[date, float]:
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as resp:
        doc = json.loads(resp.read().decode("utf-8"))
    res = doc["chart"]["result"][0]
    out: dict[date, float] = {}
    closes = res["indicators"]["quote"][0]["close"]
    for ts, close in zip(res["timestamp"], closes):
        if close is None:
            continue
        day = datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
        out[day] = float(close)
    if len(out) < 400:
        raise RuntimeError(f"Yahoo 日线过短: {len(out)}")
    return out


def nearest_on_or_before(closes: dict[date, float], day: date, limit: int = 3) -> tuple[date, float] | None:
    for i in range(limit + 1):
        cur = day - timedelta(days=i)
        if cur in closes:
            return cur, closes[cur]
    return None


def build_weeks(closes: dict[date, float]) -> list[dict[str, Any]]:
    sundays = sorted(d for d in closes if d.weekday() == 6)
    rows: list[dict[str, Any]] = []
    for sunday in sundays:
        prev_day = sunday - timedelta(days=7)
        start = nearest_on_or_before(closes, prev_day)
        end = nearest_on_or_before(closes, sunday, limit=0)
        if not start or not end:
            continue
        start_day, start_px = start
        end_day, end_px = end
        if (end_day - start_day).days < 5 or start_px <= 0:
            continue
        usd = end_px - start_px
        pct = usd / start_px * 100.0
        rows.append(
            {
                "week_end": end_day.isoformat(),
                "week_start": (end_day - timedelta(days=6)).isoformat(),
                "start_day": start_day.isoformat(),
                "start_px": start_px,
                "end_px": end_px,
                "usd": usd,
                "pct": pct,
            }
        )
    by_usd = sorted(rows, key=lambda r: -float(r["usd"]))
    by_pct = sorted(rows, key=lambda r: -float(r["pct"]))
    usd_rank = {r["week_end"]: i for i, r in enumerate(by_usd, 1)}
    pct_rank = {r["week_end"]: i for i, r in enumerate(by_pct, 1)}
    for row in rows:
        end = date.fromisoformat(row["week_end"])
        prior_pct = [float(r["pct"]) for r in rows if date.fromisoformat(r["week_end"]) < end and date.fromisoformat(r["week_end"]) >= SINCE_PCT]
        row["usd_rank"] = usd_rank[row["week_end"]]
        row["pct_rank"] = pct_rank[row["week_end"]]
        row["best_pct_since_2023_03"] = bool(prior_pct) and float(row["pct"]) >= max(prior_pct) and end >= SINCE_PCT
        prior_usd = [float(r["usd"]) for r in rows if r["week_end"] < row["week_end"]]
        row["new_usd_high"] = bool(prior_usd) and float(row["usd"]) > max(prior_usd)
    return rows


def main() -> None:
    print("[btc-weekly-move] fetch Yahoo BTC-USD daily…")
    closes = fetch_closes()
    weeks = build_weeks(closes)
    if not weeks:
        raise RuntimeError("没有可用周")
    latest = weeks[-1]
    payload = {
        "updated_at": now_iso(),
        "source": "yahoo",
        "symbol": "BTC-USD",
        "definition": "上周日收盘到本周日收盘（UTC 日），缺日最多回看 3 天。",
        "disclaimer": "Yahoo BTC-USD 日线口径，与 Galaxy 等第三方小时点可能不一致。",
        "week_count": len(weeks),
        "latest": latest,
        "weeks": weeks,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(
        f"[btc-weekly-move] weeks={len(weeks)} latest={latest['week_end']} "
        f"usd={latest['usd']:.0f} pct={latest['pct']:.1f} usd_rank={latest['usd_rank']} "
        f"pct_rank={latest['pct_rank']}"
    )


if __name__ == "__main__":
    main()
