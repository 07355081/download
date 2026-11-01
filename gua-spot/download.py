#!/usr/bin/env python3
"""GUA / Superfortune 行情快照（CoinGecko 免费/Demo）。

输出：output/json/gua_spot.json
不占 Dune。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_PATH = HERE / "output" / "json" / "gua_spot.json"
COIN_ID = "superfortune"
UA = "wublock-gua-spot/1.0"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("COINGECKO_KEY", "COINGECKO_DEMO_API_KEY"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def http_json(url: str, headers: dict[str, str]) -> Any:
    req = urllib.request.Request(
        url, headers={**headers, "User-Agent": UA, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cg_get(path: str, params: dict[str, str], env: dict[str, str]) -> Any:
    key = (env.get("COINGECKO_KEY") or env.get("COINGECKO_DEMO_API_KEY") or "").strip()
    qs = urllib.parse.urlencode(params)
    attempts: list[tuple[str, dict[str, str]]] = []
    if key:
        attempts.append(
            (f"https://api.coingecko.com/api/v3{path}?{qs}", {"x-cg-demo-api-key": key})
        )
        attempts.append(
            (f"https://pro-api.coingecko.com/api/v3{path}?{qs}", {"x-cg-pro-api-key": key})
        )
    attempts.append((f"https://api.coingecko.com/api/v3{path}?{qs}", {}))
    last: Exception | None = None
    for url, headers in attempts:
        try:
            return http_json(url, headers)
        except Exception as exc:  # noqa: BLE001
            last = exc
            continue
    raise RuntimeError(f"CoinGecko 失败: {last}")


def fmt_price(value: float) -> float:
    if value >= 1:
        return round(value, 4)
    if value >= 0.1:
        return round(value, 4)
    return round(value, 6)


def main() -> int:
    env = load_env()
    doc = cg_get(
        f"/coins/{COIN_ID}",
        {
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "false",
            "developer_data": "false",
        },
        env,
    )
    if not isinstance(doc, dict):
        raise RuntimeError("CoinGecko 返回不是对象")
    md = doc.get("market_data") or {}
    if not isinstance(md, dict) or not md:
        raise RuntimeError("无 market_data")

    price = float((md.get("current_price") or {}).get("usd") or 0)
    if price <= 0:
        raise RuntimeError("无有效美元价")
    chg24 = md.get("price_change_percentage_24h")
    chg7 = md.get("price_change_percentage_7d")
    high24 = (md.get("high_24h") or {}).get("usd")
    low24 = (md.get("low_24h") or {}).get("usd")
    mcap = (md.get("market_cap") or {}).get("usd")
    ath = (md.get("ath") or {}).get("usd")
    ath_chg = (md.get("ath_change_percentage") or {}).get("usd")

    # 近 7 日高点：用 market_chart 日点近似
    chart = cg_get(
        f"/coins/{COIN_ID}/market_chart",
        {"vs_currency": "usd", "days": "90"},
        env,
    )
    prices = chart.get("prices") if isinstance(chart, dict) else None
    week_high = None
    week_high_ts = None
    if isinstance(prices, list) and prices:
        for row in prices:
            if not isinstance(row, list) or len(row) < 2:
                continue
            px = float(row[1])
            if week_high is None or px > week_high:
                week_high = px
                week_high_ts = int(row[0])
    daily: dict[str, float] = {}
    if isinstance(prices, list):
        for row in prices:
            if not isinstance(row, list) or len(row) < 2:
                continue
            day = datetime.fromtimestamp(int(row[0]) / 1000, timezone.utc).strftime("%Y-%m-%d")
            daily[day] = float(row[1])
    drop_week = None
    week_high_day = None
    if week_high and week_high > 0:
        drop_week = (price / week_high - 1) * 100
        if week_high_ts:
            week_high_day = datetime.fromtimestamp(
                week_high_ts / 1000, timezone.utc
            ).strftime("%Y-%m-%d")

    payload = {
        "updated_at": now_iso(),
        "source": "CoinGecko /coins/superfortune",
        "coin_id": COIN_ID,
        "symbol": str(doc.get("symbol") or "gua").upper(),
        "name": doc.get("name") or "Superfortune",
        "live": {
            "asof": now_iso(),
            "price": fmt_price(price),
            "chg_24h_pct": round(float(chg24), 1) if chg24 is not None else None,
            "chg_7d_pct": round(float(chg7), 1) if chg7 is not None else None,
            "high_24h": fmt_price(float(high24)) if high24 else None,
            "low_24h": fmt_price(float(low24)) if low24 else None,
            "market_cap_usd": float(mcap) if mcap is not None else None,
            "ath_usd": float(ath) if ath is not None else None,
            "ath_change_pct": round(float(ath_chg), 1) if ath_chg is not None else None,
            "week_high": fmt_price(week_high) if week_high else None,
            "week_high_day": week_high_day,
            "drop_from_week_high_pct": round(drop_week, 1) if drop_week is not None else None,
        },
        "data": [{"day": day, "value": fmt_price(px)} for day, px in sorted(daily.items())],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    live = payload["live"]
    print(
        f"wrote {OUT_PATH} price={live['price']} chg24={live['chg_24h_pct']} "
        f"mcap={live['market_cap_usd']} week_drop={live['drop_from_week_high_pct']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
