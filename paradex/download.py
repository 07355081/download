# -*- coding: utf-8 -*-
"""Paradex：DefiLlama 免费协议锁仓与费用，CoinGecko 免费 DIME 行情。

https://api.llama.fi/protocol/paradex
https://api.llama.fi/summary/fees/paradex
https://api.coingecko.com/api/v3/coins/paradex
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "output" / "json" / "paradex.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
UA = {"User-Agent": "dashboard-data/paradex", "Accept": "application/json"}
TZ_CN = timezone(timedelta(hours=8))
PAGE = "https://defillama.com/protocol/paradex"
GECKO_ID = "paradex"


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
    for key in ("COINGECKO_KEY", "COINGECKO_DEMO_API_KEY", "COINGECKO_API_KEY"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def num(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def cn_day(ts: int) -> str:
    return datetime.fromtimestamp(ts, TZ_CN).strftime("%Y-%m-%d")


def utc_day(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def tvl_rows(proto: dict[str, Any]) -> list[tuple[str, float]]:
    by_day: dict[str, float] = {}
    for item in proto.get("tvl") or []:
        if not isinstance(item, dict):
            continue
        ts = int(num(item.get("date")))
        usd = num(item.get("totalLiquidityUSD"))
        if ts <= 0 or usd <= 0:
            continue
        by_day[cn_day(ts)] = usd
    return sorted(by_day.items())


def tvl_latest(proto: dict[str, Any]) -> tuple[str, float, str, float]:
    rows = tvl_rows(proto)
    if not rows:
        raise RuntimeError("DefiLlama 没有 Paradex 锁仓")
    latest_day, latest = rows[-1]
    peak_day, peak = max(rows, key=lambda row: row[1])
    return latest_day, latest, peak_day, peak


def fee_rows(summary: dict[str, Any]) -> list[tuple[str, float]]:
    chart = summary.get("totalDataChart") or []
    by_day: dict[str, float] = {}
    if not isinstance(chart, list):
        return []
    for row in chart:
        if not isinstance(row, list) or len(row) < 2:
            continue
        amount = num(row[1])
        if amount < 0:
            continue
        by_day[utc_day(int(num(row[0])))] = amount
    return sorted(by_day.items())


def fees_24h(summary: dict[str, Any]) -> tuple[str, float]:
    rows = fee_rows(summary)
    if not rows:
        raise RuntimeError("DefiLlama 没有 Paradex 费用")
    day, last_amount = rows[-1]
    amount = num(summary.get("total24h"))
    if amount <= 0:
        amount = last_amount
    if amount < 0:
        raise RuntimeError("Paradex 费用异常")
    return day, amount


def as_series(rows: list[tuple[str, float]]) -> list[dict[str, Any]]:
    return [{"month": day, "usd": round(usd, 2)} for day, usd in rows]


def gecko_dime(env: dict[str, str]) -> dict[str, Any]:
    url = (
        "https://api.coingecko.com/api/v3/coins/"
        f"{GECKO_ID}?localization=false&tickers=false&community_data=false&developer_data=false"
    )
    key = (
        env.get("COINGECKO_KEY")
        or env.get("COINGECKO_DEMO_API_KEY")
        or env.get("COINGECKO_API_KEY")
        or ""
    ).strip()
    attempts: list[dict[str, str]] = []
    if key:
        attempts.append({"x-cg-demo-api-key": key})
        attempts.append({"x-cg-pro-api-key": key})
    attempts.append({})
    last: Exception | None = None
    coin: Any = None
    for headers in attempts:
        try:
            coin = get_json(url, headers)
            if isinstance(coin, dict) and coin.get("id"):
                break
        except Exception as exc:  # noqa: BLE001
            last = exc
            coin = None
    if not isinstance(coin, dict):
        raise RuntimeError(f"CoinGecko DIME 失败: {last}")
    market = coin.get("market_data") if isinstance(coin.get("market_data"), dict) else {}
    mcap = num((market.get("market_cap") or {}).get("usd"))
    volume = num((market.get("total_volume") or {}).get("usd"))
    price = num((market.get("current_price") or {}).get("usd"))
    if mcap <= 0:
        raise RuntimeError("DIME 市值为空")
    return {
        "symbol": str(coin.get("symbol") or "dime").upper(),
        "gecko_id": GECKO_ID,
        "mcap_usd": round(mcap, 2),
        "volume_24h_usd": round(volume, 2),
        "price_usd": round(price, 8),
    }


def main() -> int:
    proto = get_json("https://api.llama.fi/protocol/paradex")
    fees = get_json("https://api.llama.fi/summary/fees/paradex")
    if not isinstance(proto, dict) or not isinstance(fees, dict):
        raise RuntimeError("DefiLlama 返回异常")
    day, tvl, peak_day, peak = tvl_latest(proto)
    fee_day, fee = fees_24h(fees)
    token = gecko_dime(load_env())
    drop = (1 - tvl / peak) * 100 if peak > 0 else 0.0
    doc = {
        "source": "DefiLlama protocol/paradex + summary/fees/paradex；CoinGecko coins/paradex",
        "source_page": PAGE,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest": {
            "date": day,
            "tvl_usd": round(tvl, 2),
            "peak_date": peak_day,
            "peak_tvl_usd": round(peak, 2),
            "drop_pct": round(drop, 2),
            "fees_day": fee_day,
            "fees_24h_usd": round(fee, 2),
            "token": token,
        },
        "series": [
            {"label": "锁仓", "labelEn": "TVL", "months": as_series(tvl_rows(proto))},
            {"label": "协议费用", "labelEn": "Fees", "months": as_series(fee_rows(fees))},
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT)
    print(
        f"wrote {OUT.name} day={day} tvl={tvl:.0f} fees={fee:.0f} "
        f"mcap={token['mcap_usd']:.0f} vol={token['volume_24h_usd']:.0f}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
