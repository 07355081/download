"""XRP 价格、全市场期货未平仓、CME 未平仓。CoinGlass 用已有钥匙，价格用币安公开日线。

2026-08-17 的 CME 收盘必须是 283782500 枚，2026-08-31 必须是 387287500 枚，对不上就停。

输出：
  output/json/xrp_oi.json
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "coinglass-history"))
import _common as C  # noqa: E402

OUT_PATH = HERE / "output" / "json" / "xrp_oi.json"
UA = "dashboard-data/xrp-oi"
CHECK = {"2026-08-17": 283_782_500, "2026-08-31": 387_287_500}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_of(ts: int) -> str:
    if ts > 10_000_000_000:
        ts //= 1000
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def cg_series(path: str, params: dict[str, str]) -> dict[str, int]:
    status, body = C.http_get_json(C.API_BASE + path, params=params, retries=2)
    if status != 200 or not isinstance(body, dict) or str(body.get("code")) not in ("0", "0.0"):
        raise RuntimeError(f"{path} {status} {body if isinstance(body, str) else body.get('msg')}")
    out: dict[str, int] = {}
    for row in body.get("data") or []:
        if not isinstance(row, dict) or row.get("close") is None:
            continue
        out[day_of(int(row["time"]))] = int(round(float(row["close"])))
    if len(out) < 30:
        raise RuntimeError(f"{path} 只有 {len(out)} 天")
    return out


def binance_closes(start_day: str) -> dict[str, float]:
    start = datetime.strptime(start_day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.now(timezone.utc) + timedelta(days=1)
    out: dict[str, float] = {}
    cursor = start
    while cursor < end:
        qs = urllib.parse.urlencode(
            {
                "symbol": "XRPUSDT",
                "interval": "1d",
                "startTime": str(int(cursor.timestamp() * 1000)),
                "endTime": str(int(end.timestamp() * 1000)),
                "limit": "1000",
            }
        )
        req = urllib.request.Request(
            "https://api.binance.com/api/v3/klines?" + qs,
            headers={"User-Agent": UA},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
        if not rows:
            break
        for row in rows:
            out[day_of(int(row[0]))] = float(row[4])
        last = datetime.fromtimestamp(int(rows[-1][0]) / 1000, timezone.utc)
        nxt = last + timedelta(days=1)
        if nxt <= cursor:
            break
        cursor = nxt
        time.sleep(0.2)
    return out


def main() -> None:
    print("[xrp-oi] fetch CoinGlass…")
    cme = cg_series(
        "/api/futures/open-interest/history",
        {"exchange": "CME", "symbol": "XRPUSD", "interval": "1d", "unit": "coin", "limit": "1000"},
    )
    time.sleep(0.3)
    glob = cg_series(
        "/api/futures/open-interest/aggregated-history",
        {"symbol": "XRP", "interval": "1d", "unit": "coin", "limit": "1000"},
    )
    for day, expect in CHECK.items():
        if cme.get(day) != expect:
            raise SystemExit(f"{day} CME 是 {cme.get(day)}，不是 {expect}")
    print("[xrp-oi] fetch Binance price…")
    price = binance_closes(min(cme))
    days = sorted(set(cme) & set(glob) & set(price))
    history = []
    for day in days:
        cme_oi = cme[day]
        global_oi = glob[day]
        if global_oi <= 0:
            continue
        history.append(
            {
                "time": day,
                "price": round(price[day], 4),
                "cme_oi": cme_oi,
                "global_oi": global_oi,
                "others_oi": global_oi - cme_oi,
                "share_pct": round(cme_oi / global_oi * 100, 2),
            }
        )
    if len(history) < 30:
        raise SystemExit(f"天数太少: {len(history)}")
    last = history[-1]
    snap = {
        "name": "xrp_oi",
        "source": "coinglass open-interest CME XRPUSD + aggregated XRP, binance XRPUSDT",
        "note_zh": "XRP 日价格、全市场期货未平仓枚数、CME 未平仓枚数和份额。",
        "updated_at": now_iso(),
        "as_of": last["time"],
        "history": history,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[xrp-oi] {len(history)} days as_of={last['time']} "
        f"price={last['price']} cme={last['cme_oi']} share={last['share_pct']}"
    )


if __name__ == "__main__":
    main()
