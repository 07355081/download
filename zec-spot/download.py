#!/usr/bin/env python3
"""ZEC 现货与合约快照。

价格用 OKX 公开 ZEC-USDT。市值、合约成交、未平仓、爆仓用已有 CoinGlass coins-markets。
不新开付费额度，不占 Dune。外电数字对不上时以这两处为准。

输出：output/json/zec_spot.json
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "coinglass-history"))
import _common as C  # noqa: E402

OUT_PATH = HERE / "output" / "json" / "zec_spot.json"
UA = "dashboard-data/zec-spot"
OKX = "https://www.okx.com/api/v5/market/history-candles?instId=ZEC-USDT&bar=1Dutc&limit=200"
MOVE_PCT = 20.0
LEVEL = 1000.0


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("OKX 返回不是对象")
    return payload


def day_of(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def okx_days() -> list[dict[str, Any]]:
    payload = http_json(OKX)
    rows = []
    for bar in payload.get("data") or []:
        if not isinstance(bar, list) or len(bar) < 5:
            continue
        rows.append({
            "day": day_of(int(bar[0])),
            "open": float(bar[1]),
            "high": float(bar[2]),
            "low": float(bar[3]),
            "close": float(bar[4]),
        })
    rows.sort(key=lambda item: item["day"])
    if len(rows) < 91:
        raise RuntimeError(f"OKX 日线只有 {len(rows)} 根")
    prev = None
    for row in rows:
        close = row["close"]
        row["chg_pct"] = round((close / prev - 1) * 100, 1) if prev else None
        row["cross_1000"] = bool(prev is not None and prev < LEVEL <= row["high"])
        prev = close
    return rows


def coinglass_zec() -> dict[str, Any]:
    status, payload = C.http_get_json(C.API_BASE + "/api/futures/coins-markets")
    if status != 200 or not isinstance(payload, dict) or str(payload.get("code")) not in ("0", "0.0"):
        raise RuntimeError(f"CoinGlass coins-markets {status} {payload if isinstance(payload, str) else payload.get('msg')}")
    for row in payload.get("data") or []:
        if isinstance(row, dict) and str(row.get("symbol") or "").upper() == "ZEC":
            long_vol = float(row.get("long_volume_usd_24h") or 0)
            short_vol = float(row.get("short_volume_usd_24h") or 0)
            return {
                "price": float(row.get("current_price") or 0),
                "chg_24h_pct": float(row.get("price_change_percent_24h") or 0),
                "market_cap_usd": float(row.get("market_cap_usd") or 0),
                "open_interest_usd": float(row.get("open_interest_usd") or 0),
                "volume_usd_24h": long_vol + short_vol,
                "liquidation_usd_24h": float(row.get("liquidation_usd_24h") or 0),
                "long_liquidation_usd_24h": float(row.get("long_liquidation_usd_24h") or 0),
                "short_liquidation_usd_24h": float(row.get("short_liquidation_usd_24h") or 0),
            }
    raise RuntimeError("CoinGlass 这次返回的币里没有 ZEC")


def ret_from(days: list[dict[str, Any]], live: float, back: int) -> dict[str, Any]:
    base = days[-1 - back]
    return {
        "pct": round((live / base["close"] - 1) * 100, 1),
        "from_day": base["day"],
        "from_close": round(base["close"], 2),
    }


def main() -> int:
    days = okx_days()
    cg = coinglass_zec()
    ticker = http_json("https://www.okx.com/api/v5/market/ticker?instId=ZEC-USDT")
    spot = (ticker.get("data") or [{}])[0]
    last = float(spot.get("last") or cg["price"])
    open24 = float(spot.get("open24h") or 0)
    chg24 = (last / open24 - 1) * 100 if open24 else cg["chg_24h_pct"]
    today = datetime.now(timezone.utc).date().isoformat()
    payload = {
        "updated_at": now_iso(),
        "source": "OKX ZEC-USDT 现货 + CoinGlass futures coins-markets",
        "note": (
            "日线是 OKX 1Dutc。24 小时涨跌用 OKX 现货 open24h。"
            "市值、合约成交、未平仓、爆仓用 CoinGlass 当前快照，没有更早的存档。"
            "日收盘涨跌达到 20%，或盘中第一次升破 1000 美元，才做历史阈值。"
        ),
        "move_pct": MOVE_PCT,
        "level": LEVEL,
        "live": {
            "asof": now_iso(),
            "okx_price": round(last, 2),
            "chg_24h_pct": round(chg24, 1),
            "market_cap_usd": cg["market_cap_usd"],
            "open_interest_usd": cg["open_interest_usd"],
            "volume_usd_24h": cg["volume_usd_24h"],
            "liquidation_usd_24h": cg["liquidation_usd_24h"],
            "long_liquidation_usd_24h": cg["long_liquidation_usd_24h"],
            "short_liquidation_usd_24h": cg["short_liquidation_usd_24h"],
            "ret_30d": ret_from(days, last, 30),
            "ret_90d": ret_from(days, last, 90),
        },
        "days": [row for row in days if row["day"] < today],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(OUT_PATH)
    live = payload["live"]
    print(
        f"wrote {OUT_PATH} price={live['okx_price']} chg24={live['chg_24h_pct']} "
        f"mcap={live['market_cap_usd']:.0f} oi={live['open_interest_usd']:.0f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
