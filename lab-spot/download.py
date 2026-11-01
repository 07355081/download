#!/usr/bin/env python3
"""LAB 行情快照：OKX 永续 + CoinGlass 费率/爆仓。

OKX 现货未上架 LAB-USDT，价格走永续 LAB-USDT-SWAP。
CoinGlass：Binance LABUSDT 资金费率、OI、成交；全网 LAB 24h 爆仓与排名。
不占 Dune。

输出：output/json/lab_spot.json
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

OUT_PATH = HERE / "output" / "json" / "lab_spot.json"
UA = "Mozilla/5.0 (compatible; dashboard-data/lab-spot)"
INST = "LAB-USDT-SWAP"
TICKER_URL = f"https://www.okx.com/api/v5/market/ticker?instId={INST}"
CANDLES_URL = (
    f"https://www.okx.com/api/v5/market/history-candles?instId={INST}&bar=1Dutc&limit=30"
)


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


def candles() -> list[dict[str, Any]]:
    payload = http_json(CANDLES_URL)
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
    if len(rows) < 8:
        raise RuntimeError(f"OKX 日线只有 {len(rows)} 根")
    prev = None
    for row in rows:
        close = row["close"]
        row["chg_pct"] = round((close / prev - 1) * 100, 1) if prev else None
        prev = close
    return rows


def funding_display_pct(raw: float) -> float:
    """CoinGlass 有时给小数费率、有时给百分数。统一成展示用百分数。"""
    if abs(raw) >= 1:
        return round(raw, 4)
    return round(raw * 100.0, 4)


def coinglass_lab() -> dict[str, Any]:
    st, pairs = C.http_get_json(C.API_BASE + "/api/futures/pairs-markets?symbol=LAB")
    if st != 200 or not isinstance(pairs, dict) or str(pairs.get("code")) not in ("0", "0.0"):
        raise RuntimeError(f"CoinGlass pairs-markets {st}")
    rows = [r for r in (pairs.get("data") or []) if isinstance(r, dict)]
    binance = next((r for r in rows if str(r.get("exchange_name") or "") == "Binance"), None)
    if not binance:
        raise RuntimeError("CoinGlass pairs-markets 无 Binance LABUSDT")

    st2, liq_doc = C.http_get_json(C.API_BASE + "/api/futures/liquidation/coin-list?range=24h")
    if st2 != 200 or not isinstance(liq_doc, dict) or str(liq_doc.get("code")) not in ("0", "0.0"):
        raise RuntimeError(f"CoinGlass liquidation coin-list {st2}")
    liq_rows = [r for r in (liq_doc.get("data") or []) if isinstance(r, dict)]

    def liq_usd(row: dict[str, Any]) -> float:
        return float(row.get("liquidation_usd_24h") or row.get("total_liquidation_usd") or 0)

    ranked = sorted(liq_rows, key=liq_usd, reverse=True)
    lab_liq = next((r for r in ranked if str(r.get("symbol") or "").upper() == "LAB"), None)
    rank = None
    if lab_liq is not None:
        rank = ranked.index(lab_liq) + 1
    top3 = [str(r.get("symbol") or "") for r in ranked[:3]]

    funding_raw = float(binance.get("funding_rate") or 0)
    long_vol = float(binance.get("long_volume_usd") or 0)
    short_vol = float(binance.get("short_volume_usd") or 0)
    bn_liq = float(binance.get("long_liquidation_usd_24h") or 0) + float(
        binance.get("short_liquidation_usd_24h") or 0
    )
    return {
        "binance_price": float(binance.get("current_price") or 0),
        "binance_chg_24h_pct": float(binance.get("price_change_percent_24h") or 0),
        "binance_funding_rate_raw": funding_raw,
        "binance_funding_rate_pct": funding_display_pct(funding_raw),
        "binance_open_interest_usd": float(binance.get("open_interest_usd") or 0),
        "binance_volume_usd_24h": long_vol + short_vol,
        "binance_liquidation_usd_24h": bn_liq,
        "liquidation_usd_24h": liq_usd(lab_liq) if lab_liq else 0.0,
        "long_liquidation_usd_24h": float((lab_liq or {}).get("long_liquidation_usd_24h") or 0),
        "short_liquidation_usd_24h": float((lab_liq or {}).get("short_liquidation_usd_24h") or 0),
        "liquidation_rank_24h": rank,
        "liquidation_top3": top3,
    }


def main() -> int:
    days = candles()
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
    cg = coinglass_lab()
    payload = {
        "updated_at": now_iso(),
        "source": "OKX LAB-USDT-SWAP + CoinGlass pairs-markets/liquidation coin-list",
        "inst_id": INST,
        "note": (
            "价格以 OKX 永续为准（现货未上架）。"
            "资金费率、合约成交、OI 取 Binance LABUSDT。"
            "24h 爆仓与排名取 CoinGlass 全网 coin-list。"
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
            **cg,
        },
        "days": [row for row in days if row["day"] < today],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(OUT_PATH)
    live = payload["live"]
    print(
        f"wrote {OUT_PATH} price={live['price']} chg24={live['chg_24h_pct']} "
        f"funding_pct={live['binance_funding_rate_pct']} "
        f"liq={live['liquidation_usd_24h']:.0f} rank={live['liquidation_rank_24h']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
