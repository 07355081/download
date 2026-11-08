#!/usr/bin/env python3
"""EDGE 行情快照：OKX 永续 + CoinGlass 费率/爆仓。

价格走 OKX EDGE-USDT-SWAP（现货亦有，永续对齐外电）。
含近 1 小时相对高点回撤（用 5 分钟 K）。
CoinGlass：Binance EDGEUSDT 费率/OI；全网 EDGE 1h/24h 爆仓与排名。
不占 Dune。

输出：output/json/edge_spot.json
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

OUT_PATH = HERE / "output" / "json" / "edge_spot.json"
UA = "Mozilla/5.0 (compatible; dashboard-data/edge-spot)"
INST = "EDGE-USDT-SWAP"
TICKER_URL = f"https://www.okx.com/api/v5/market/ticker?instId={INST}"
CANDLES_1D = (
    f"https://www.okx.com/api/v5/market/history-candles?instId={INST}&bar=1Dutc&limit=30"
)
CANDLES_5M = f"https://www.okx.com/api/v5/market/candles?instId={INST}&bar=5m&limit=24"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("OKX 返回不是对象")
    if str(payload.get("code")) != "0":
        raise RuntimeError(f"OKX 错误: {payload}")
    return payload


def day_of(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def fmt_price(value: float) -> float:
    if value >= 1:
        return round(value, 4)
    if value >= 0.1:
        return round(value, 4)
    return round(value, 6)


def candles_1d() -> list[dict[str, Any]]:
    payload = http_json(CANDLES_1D)
    rows: list[dict[str, Any]] = []
    for bar in payload.get("data") or []:
        if not isinstance(bar, list) or len(bar) < 5:
            continue
        rows.append(
            {
                "day": day_of(int(bar[0])),
                "open": float(bar[1]),
                "high": float(bar[2]),
                "low": float(bar[3]),
                "close": float(bar[4]),
            }
        )
    rows.sort(key=lambda item: item["day"])
    if len(rows) < 5:
        raise RuntimeError(f"OKX 日线只有 {len(rows)} 根")
    prev = None
    for row in rows:
        close = row["close"]
        row["chg_pct"] = round((close / prev - 1) * 100, 1) if prev else None
        prev = close
    return rows


def hour_drop(last: float) -> dict[str, Any]:
    """近约 1 小时：相对 5m K 最高价回撤、相对开盘涨跌。"""
    payload = http_json(CANDLES_5M)
    bars = []
    for bar in payload.get("data") or []:
        if not isinstance(bar, list) or len(bar) < 5:
            continue
        bars.append(
            {
                "ts": int(bar[0]),
                "open": float(bar[1]),
                "high": float(bar[2]),
                "low": float(bar[3]),
                "close": float(bar[4]),
            }
        )
    bars.sort(key=lambda item: item["ts"])
    window = bars[-12:]  # 12*5m ≈ 1h
    if not window:
        return {"drop_1h_from_high_pct": None, "chg_1h_pct": None, "high_1h": None, "low_1h": None}
    high = max(float(b["high"]) for b in window)
    low = min(float(b["low"]) for b in window)
    open_1h = float(window[0]["open"])
    drop = ((last / high) - 1) * 100 if high > 0 else None
    chg = ((last / open_1h) - 1) * 100 if open_1h > 0 else None
    return {
        "drop_1h_from_high_pct": round(drop, 1) if drop is not None else None,
        "chg_1h_pct": round(chg, 1) if chg is not None else None,
        "high_1h": fmt_price(high),
        "low_1h": fmt_price(low),
    }


def funding_display_pct(raw: float) -> float:
    if abs(raw) >= 1:
        return round(raw, 4)
    return round(raw * 100.0, 4)


def coinglass_edge() -> dict[str, Any]:
    st, pairs = C.http_get_json(C.API_BASE + "/api/futures/pairs-markets?symbol=EDGE")
    if st != 200 or not isinstance(pairs, dict) or str(pairs.get("code")) not in ("0", "0.0"):
        raise RuntimeError(f"CoinGlass pairs-markets {st}")
    rows = [r for r in (pairs.get("data") or []) if isinstance(r, dict)]
    binance = next((r for r in rows if str(r.get("exchange_name") or "") == "Binance"), None)

    st2, liq_doc = C.http_get_json(C.API_BASE + "/api/futures/liquidation/coin-list?range=24h")
    if st2 != 200 or not isinstance(liq_doc, dict) or str(liq_doc.get("code")) not in ("0", "0.0"):
        raise RuntimeError(f"CoinGlass liquidation coin-list {st2}")
    liq_rows = [r for r in (liq_doc.get("data") or []) if isinstance(r, dict)]

    def liq_usd(row: dict[str, Any]) -> float:
        return float(row.get("liquidation_usd_24h") or row.get("total_liquidation_usd") or 0)

    ranked = sorted(liq_rows, key=liq_usd, reverse=True)
    edge_liq = next((r for r in ranked if str(r.get("symbol") or "").upper() == "EDGE"), None)
    rank = ranked.index(edge_liq) + 1 if edge_liq is not None else None
    top3 = [str(r.get("symbol") or "") for r in ranked[:3]]

    out: dict[str, Any] = {
        "liquidation_usd_24h": liq_usd(edge_liq) if edge_liq else 0.0,
        "long_liquidation_usd_24h": float((edge_liq or {}).get("long_liquidation_usd_24h") or 0),
        "short_liquidation_usd_24h": float((edge_liq or {}).get("short_liquidation_usd_24h") or 0),
        "liquidation_usd_1h": float((edge_liq or {}).get("liquidation_usd_1h") or 0),
        "long_liquidation_usd_1h": float((edge_liq or {}).get("long_liquidation_usd_1h") or 0),
        "short_liquidation_usd_1h": float((edge_liq or {}).get("short_liquidation_usd_1h") or 0),
        "liquidation_rank_24h": rank,
        "liquidation_top3": top3,
    }
    if binance:
        funding_raw = float(binance.get("funding_rate") or 0)
        long_vol = float(binance.get("long_volume_usd") or 0)
        short_vol = float(binance.get("short_volume_usd") or 0)
        out.update(
            {
                "binance_price": float(binance.get("current_price") or 0),
                "binance_chg_24h_pct": float(binance.get("price_change_percent_24h") or 0),
                "binance_funding_rate_raw": funding_raw,
                "binance_funding_rate_pct": funding_display_pct(funding_raw),
                "binance_open_interest_usd": float(binance.get("open_interest_usd") or 0),
                "binance_volume_usd_24h": long_vol + short_vol,
            }
        )
    else:
        out.update(
            {
                "binance_price": None,
                "binance_chg_24h_pct": None,
                "binance_funding_rate_raw": None,
                "binance_funding_rate_pct": None,
                "binance_open_interest_usd": None,
                "binance_volume_usd_24h": None,
            }
        )
    return out


def main() -> int:
    days = candles_1d()
    ticker = (http_json(TICKER_URL).get("data") or [{}])[0]
    last = float(ticker.get("last") or 0)
    open24 = float(ticker.get("open24h") or 0)
    high24 = float(ticker.get("high24h") or 0)
    low24 = float(ticker.get("low24h") or 0)
    if last <= 0:
        raise RuntimeError("OKX ticker 无有效现价")
    chg24 = (last / open24 - 1) * 100 if open24 else 0.0
    today = datetime.now(timezone.utc).date().isoformat()
    window = [row for row in days if row["day"] < today][-7:]
    week_high = max((float(row["high"]) for row in window), default=0.0)
    week_high_day = ""
    for row in window:
        if float(row["high"]) == week_high:
            week_high_day = str(row["day"])
    drop_from_week = ((last / week_high) - 1) * 100 if week_high > 0 else None
    hour = hour_drop(last)
    cg = coinglass_edge()
    payload = {
        "updated_at": now_iso(),
        "source": "OKX EDGE-USDT-SWAP + CoinGlass pairs-markets/liquidation coin-list",
        "inst_id": INST,
        "note": (
            "价格以 OKX 永续为准。"
            "近 1 小时回撤用 OKX 5 分钟 K 近 12 根。"
            "资金费率/OI 取 Binance EDGEUSDT（若有）。"
            "爆仓取 CoinGlass 全网 coin-list。"
            "不回扫外电历史窗口。"
        ),
        "live": {
            "asof": now_iso(),
            "price": fmt_price(last),
            "chg_24h_pct": round(chg24, 1),
            "high_24h": fmt_price(high24) if high24 else None,
            "low_24h": fmt_price(low24) if low24 else None,
            "open_24h": fmt_price(open24) if open24 else None,
            "week_high": fmt_price(week_high) if week_high else None,
            "week_high_day": week_high_day or None,
            "drop_from_week_high_pct": round(drop_from_week, 1) if drop_from_week is not None else None,
            **hour,
            **cg,
        },
        "days": [row for row in days if row["day"] < today],
        "series": [
            {
                "label": "EDGE 日收",
                "labelEn": "EDGE daily close",
                "months": [
                    {"month": row["day"], "usd": row["close"]}
                    for row in days
                    if row["day"] < today
                ],
            }
        ],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    live = payload["live"]
    print(
        f"wrote {OUT_PATH} price={live['price']} chg24={live['chg_24h_pct']} "
        f"drop1h={live.get('drop_1h_from_high_pct')} "
        f"liq24={live['liquidation_usd_24h']:.0f} liq1h={live['liquidation_usd_1h']:.0f} "
        f"rank={live['liquidation_rank_24h']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
