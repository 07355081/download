"""Scroll DeFi 锁仓 + 链收入（DefiLlama 免费，不占 Dune）。

锁仓：GET /v2/historicalChainTvl/Scroll
链收入：GET /summary/fees/scroll?dataType=dailyRevenue
        这条链自己的手续费减去提交以太坊的成本，可以为负。
        不要用 /overview/fees/scroll，那是链上各项目手续费合计。

输出：
  output/json/scroll_l2.json
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "scroll_l2.json"
API = "https://api.llama.fi"
UA = "dashboard-data/scroll-l2"
TIMEOUT = 90
ATTEMPTS = 3
WINDOW = 30


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


def day_of(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def parse_tvl(rows: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = row.get("date")
        tvl = row.get("tvl")
        if isinstance(ts, (int, float)) and isinstance(tvl, (int, float)):
            out[day_of(int(ts))] = float(tvl)
    return out


def parse_revenue(doc: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    chart = doc.get("totalDataChart") if isinstance(doc, dict) else None
    if not isinstance(chart, list):
        return out
    for pt in chart:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2 and isinstance(pt[0], (int, float)):
            val = pt[1]
            if isinstance(val, (int, float)):
                out[day_of(int(pt[0]))] = float(val)
    return out


def shift(day: str, n: int) -> str:
    base = datetime.strptime(day, "%Y-%m-%d").date()
    return (base - timedelta(days=n)).isoformat()


def window_sum(revenue: dict[str, float], day: str) -> float | None:
    total = 0.0
    for i in range(WINDOW):
        key = shift(day, i)
        if key not in revenue:
            return None
        total += revenue[key]
    return total


def main() -> None:
    print("[scroll-l2] fetch historicalChainTvl/Scroll…")
    tvl = parse_tvl(http_get_json(f"{API}/v2/historicalChainTvl/Scroll"))
    time.sleep(0.3)
    print("[scroll-l2] fetch summary/fees/scroll dailyRevenue…")
    revenue = parse_revenue(
        http_get_json(f"{API}/summary/fees/scroll?dataType=dailyRevenue")
    )
    if not tvl:
        raise RuntimeError("Scroll 锁仓序列为空")

    peak_day, peak_tvl = max(tvl.items(), key=lambda item: item[1])
    days = sorted(set(tvl) | set(revenue))
    history: list[dict[str, Any]] = []
    for day in days:
        row: dict[str, Any] = {"time": day}
        if day in tvl:
            row["tvl"] = tvl[day]
        if day in revenue:
            row["revenue"] = revenue[day]
        rolled = window_sum(revenue, day)
        if rolled is not None:
            row["revenue_30d"] = rolled
            row["revenue_30d_avg"] = rolled / WINDOW
        history.append(row)

    latest = history[-1]["time"]
    snap = {
        "name": "scroll_l2",
        "source": "defillama/historicalChainTvl/Scroll + summary/fees/scroll?dataType=dailyRevenue",
        "note_zh": (
            "Scroll DeFi 锁仓来自 DefiLlama historicalChainTvl。"
            "链收入是这条链自己的 dailyRevenue，等于手续费减去提交以太坊的成本，可以为负。"
            "近 30 天是截止当日、含当日的 30 个自然日。"
        ),
        "updated_at": now_iso(),
        "as_of": latest,
        "peak_day": peak_day,
        "peak_tvl": peak_tvl,
        "history": history,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    last = history[-1]
    tvl_now = float(last.get("tvl") or 0)
    print(
        f"[scroll-l2] wrote {OUT_PATH} as_of={latest} "
        f"tvl={tvl_now/1e6:.2f}M peak={peak_day} {peak_tvl/1e6:.2f}M "
        f"rev30={last.get('revenue_30d')} days={len(history)}"
    )


if __name__ == "__main__":
    main()
