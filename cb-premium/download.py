# -*- coding: utf-8 -*-
"""Coinbase 比特币溢价指数（CoinGlass 日线）。

接口：GET /api/coinbase-premium-index?interval=1d
premium_rate 单位是百分比（-0.0742 表示 -0.0742%），不是小数。

输出：output/json/cb_premium.json
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "output" / "json" / "cb_premium.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
API = "https://open-api-v4.coinglass.com/api/coinbase-premium-index"
UA = "wublock-data-cb-premium/1.0"


def load_key() -> str:
    for path in ENV_CANDIDATES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().lstrip("\ufeff")
            if line.startswith("COINGLASS_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return (os.environ.get("COINGLASS_API_KEY") or "").strip()


def fetch_page(api_key: str, *, end_ms: int | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"interval": "1d", "limit": 1000}
    if end_ms is not None:
        params["end_time"] = end_ms
    url = f"{API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={"CG-API-KEY": api_key, "Accept": "application/json", "User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        doc = json.loads(resp.read().decode("utf-8"))
    if str(doc.get("code")) != "0":
        raise RuntimeError(f"CoinGlass 失败: {doc}")
    rows = doc.get("data") or []
    return [r for r in rows if isinstance(r, dict)]


def normalize(row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        ts = int(row["time"])
        rate = float(row["premium_rate"])
        prem = float(row.get("premium") or 0)
        px = float(row.get("coinbase_price") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    # CoinGlass 有时给秒、有时给毫秒
    if ts > 10_000_000_000:
        ts //= 1000
    day = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
    return {
        "day": day,
        "time": ts,
        "premium": round(prem, 4),
        "premium_rate": rate,
        "coinbase_price": round(px, 2) if px else None,
    }


def negative_streaks(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    start: str | None = None
    length = 0
    last: str | None = None
    for row in days:
        if row["premium_rate"] < 0:
            if start is None:
                start = row["day"]
                length = 1
            else:
                length += 1
            last = row["day"]
        elif start is not None and last is not None:
            out.append({"start": start, "end": last, "length": length})
            start = None
            length = 0
            last = None
    if start is not None and last is not None:
        out.append({"start": start, "end": last, "length": length, "ongoing": True})
    return out


def main() -> None:
    api_key = load_key()
    if not api_key:
        raise SystemExit("未配置 COINGLASS_API_KEY")
    by_day: dict[str, dict[str, Any]] = {}
    end_ms: int | None = None
    for page in range(20):
        raw = fetch_page(api_key, end_ms=end_ms)
        if not raw:
            break
        added = 0
        oldest_ms = None
        for item in raw:
            row = normalize(item)
            if not row:
                continue
            if row["day"] not in by_day:
                by_day[row["day"]] = row
                added += 1
            oldest_ms = min(oldest_ms, row["time"] * 1000) if oldest_ms else row["time"] * 1000
        print(f"  page {page + 1}: raw={len(raw)} new={added} total={len(by_day)}", flush=True)
        if len(raw) < 1000 or oldest_ms is None:
            break
        end_ms = int(oldest_ms) - 1
        time.sleep(0.35)
    days = sorted(by_day.values(), key=lambda r: r["day"])
    if len(days) < 60:
        raise RuntimeError(f"日线过短: {len(days)}")
    streaks = negative_streaks(days)
    ongoing = next((s for s in reversed(streaks) if s.get("ongoing")), None)
    prior_max = max((s["length"] for s in streaks if not s.get("ongoing")), default=0)
    payload = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "CoinGlass /api/coinbase-premium-index",
        "source_url": "https://www.coinglass.com/pro/i/coinbase-bitcoin-premium-index",
        "note": "premium_rate 为百分比数值。负值=Coinbase 相对 Binance 折价。日线按 UTC。",
        "row_count": len(days),
        "latest": days[-1],
        "neg_streak": ongoing["length"] if ongoing else 0,
        "neg_streak_start": ongoing["start"] if ongoing else None,
        "prior_max_neg_streak": prior_max,
        "is_record_neg_streak": bool(ongoing and ongoing["length"] > prior_max),
        "neg_streaks": streaks[-20:],
        "days": days,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(OUT)
    print(
        f"wrote {OUT} days={len(days)} latest={days[-1]['day']} "
        f"rate={days[-1]['premium_rate']} streak={payload['neg_streak']} "
        f"record={payload['is_record_neg_streak']}"
    )


if __name__ == "__main__":
    main()
