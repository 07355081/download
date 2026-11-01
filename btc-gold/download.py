"""Daily ounces of gold per bitcoin.

OKX BTC-USDT UTC daily close divided by Yahoo COMEX gold futures (GC=F).
The in-progress UTC day is dropped. No Dune. This is not a TradingView symbol.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "btc_gold_ratio.json"
UA = "Mozilla/5.0 (compatible; dashboard-data/btc-gold)"
MIN_DAYS = 100


def get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode())


def okx_btc() -> dict[str, float]:
    out: dict[str, float] = {}
    after = ""
    for _ in range(8):
        url = "https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT&bar=1Dutc&limit=100"
        if after:
            url += f"&after={after}"
        payload = get(url)
        if str(payload.get("code")) != "0":
            raise RuntimeError(f"OKX BTC failed: {payload.get('msg')}")
        rows = payload.get("data") or []
        if not rows:
            break
        for row in rows:
            day = datetime.fromtimestamp(int(row[0]) / 1000, timezone.utc).strftime("%Y-%m-%d")
            out[day] = float(row[4])
        after = str(rows[-1][0])
        if min(out) <= "2025-01-01":
            break
    return out


def yahoo_gold() -> dict[str, float]:
    start = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
    end = int(datetime.now(timezone.utc).timestamp())
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/GC%3DF"
        f"?interval=1d&period1={start}&period2={end}"
    )
    payload = get(url)
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        raise RuntimeError("Yahoo GC=F empty")
    stamps = result[0].get("timestamp") or []
    closes = ((result[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    out: dict[str, float] = {}
    for ts, close in zip(stamps, closes):
        if close is None:
            continue
        day = datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d")
        out[day] = float(close)
    return out


def main() -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    btc = okx_btc()
    gold = yahoo_gold()
    days = []
    for day in sorted(set(btc) & set(gold)):
        if day >= today or day < "2025-01-01":
            continue
        if btc[day] <= 0 or gold[day] <= 0:
            continue
        days.append(
            {
                "date": day,
                "btc_usd": round(btc[day], 2),
                "gold_usd": round(gold[day], 2),
                "ounces": round(btc[day] / gold[day], 4),
            }
        )
    if len(days) < MIN_DAYS:
        print(f"only {len(days)} days", file=sys.stderr)
        return 1
    year = days[-1]["date"][:4]
    if not any(row["date"].startswith(f"{year}-01") for row in days):
        print(f"missing January {year}", file=sys.stderr)
        return 1
    payload = {
        "code": 0,
        "msg": "success",
        "name": "btc_gold_ratio",
        "description": "Ounces of COMEX gold per bitcoin, from the OKX BTC-USDT UTC daily close and the Yahoo GC=F daily close.",
        "source": "okx-btc-usdt-1dutc+yahoo-gc-f",
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "day_count": len(days),
        "days": days,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    last = days[-1]
    print(f"wrote {OUT_PATH} days={len(days)} latest={last['date']} ounces={last['ounces']:.2f}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"btc-gold failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
