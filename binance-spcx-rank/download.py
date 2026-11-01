#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""币安 U 本位永续：SPCXUSDT 近 24h 成交额排名（官方 fapi，免费）。

对应内容：CoinGlass「Binance 交易对 24h 成交」里 SPCX 相对 BTC/ETH 的位次与放量。
口径是币安官方 ticker/klines，不是 CoinGlass 网页，数字可能不完全一致。

输出：
  output/json/binance_spcx_rank.json
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "binance_spcx_rank.json"
UA = "dashboard-data/binance-spcx-rank"
TIMEOUT = 60
SYMBOL = "SPCXUSDT"
TOP_N = 15
FAPI = "https://fapi.binance.com"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_tickers() -> list[dict[str, Any]]:
    raw = http_get_json(f"{FAPI}/fapi/v1/ticker/24hr")
    if not isinstance(raw, list):
        raise RuntimeError("ticker/24hr 非列表")
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        sym = str(item.get("symbol") or "")
        if not sym.endswith("USDT"):
            continue
        try:
            vol = float(item.get("quoteVolume") or 0)
        except (TypeError, ValueError):
            continue
        if vol <= 0:
            continue
        try:
            last = float(item.get("lastPrice") or 0)
            chg = float(item.get("priceChangePercent") or 0)
        except (TypeError, ValueError):
            last, chg = 0.0, 0.0
        rows.append(
            {
                "symbol": sym,
                "volume_usd": vol,
                "last_price": last,
                "price_change_pct_24h": chg,
            }
        )
    rows.sort(key=lambda r: float(r["volume_usd"]), reverse=True)
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    return rows


def load_daily_klines(limit: int = 10) -> list[dict[str, Any]]:
    raw = http_get_json(
        f"{FAPI}/fapi/v1/klines?symbol={SYMBOL}&interval=1d&limit={limit}"
    )
    if not isinstance(raw, list):
        raise RuntimeError("klines 非列表")
    out: list[dict[str, Any]] = []
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    for candle in raw:
        if not isinstance(candle, list) or len(candle) < 8:
            continue
        open_ms = int(candle[0])
        close_ms = open_ms + 86_400_000 - 1
        # 未收盘的当日 K 线不进「完整日」序列
        complete = close_ms < now_ms
        day = datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        try:
            quote = float(candle[7])
        except (TypeError, ValueError):
            continue
        out.append({"time": day, "volume_usd": quote, "complete": complete})
    return out


def main() -> int:
    tickers = load_tickers()
    target = next((r for r in tickers if r["symbol"] == SYMBOL), None)
    if not target:
        raise RuntimeError(f"未找到 {SYMBOL}")

    ahead = [r["symbol"] for r in tickers if int(r["rank"]) < int(target["rank"])]
    only_btc_eth = (
        int(target["rank"]) == 3
        and set(ahead) <= {"BTCUSDT", "ETHUSDT"}
        and len(ahead) == 2
    )

    days = load_daily_klines()
    complete = [d for d in days if d.get("complete")]
    last_complete = complete[-1] if complete else None
    prev_complete = complete[-2] if len(complete) >= 2 else None
    day_dod_pct = None
    if (
        last_complete
        and prev_complete
        and isinstance(last_complete.get("volume_usd"), (int, float))
        and isinstance(prev_complete.get("volume_usd"), (int, float))
        and float(prev_complete["volume_usd"]) > 0
    ):
        day_dod_pct = (
            float(last_complete["volume_usd"]) / float(prev_complete["volume_usd"]) - 1.0
        ) * 100.0

    doc = {
        "meta": {
            "source": "binance_fapi",
            "symbol": SYMBOL,
            "note": "币安 U 本位永续 24h quoteVolume 排名；完整日成交来自 1d kline。非 CoinGlass。",
            "updated_at": now_iso(),
            "as_of_utc_day": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        },
        "spcx": {
            "symbol": SYMBOL,
            "rank": int(target["rank"]),
            "volume_usd_24h": float(target["volume_usd"]),
            "last_price": float(target["last_price"]),
            "price_change_pct_24h": float(target["price_change_pct_24h"]),
            "ahead_symbols": ahead,
            "only_behind_btc_eth": only_btc_eth,
            "ticker_count": len(tickers),
        },
        "top": tickers[:TOP_N],
        "daily": {
            "last_complete": last_complete,
            "prev_complete": prev_complete,
            "day_dod_pct": day_dod_pct,
            "series": complete[-30:],
        },
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[binance-spcx-rank] rank={target['rank']} "
        f"vol24h={float(target['volume_usd']):.0f} "
        f"day_dod={day_dod_pct}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError, OSError) as exc:
        print(f"[binance-spcx-rank] ERROR {exc}", flush=True)
        raise SystemExit(1)
