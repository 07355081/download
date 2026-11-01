#!/usr/bin/env python3
"""STRC 股价 + Apyx apxUSD 流通市值快照（免费）。

- STRC：Yahoo Finance chart
- apxUSD：DefiLlama stablecoins.llama.fi/stablecoin/354
不占 Dune。

输出：output/json/strc_apxusd.json
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "strc_apxusd.json"
UA = "dashboard-data/strc-apxusd"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/STRC?range=3mo&interval=1d"
LLAMA = "https://stablecoins.llama.fi/stablecoin/354"
PAR = 100.0


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_strc() -> dict[str, Any]:
    doc = http_json(YAHOO)
    res = ((doc.get("chart") or {}).get("result") or [None])[0]
    if not isinstance(res, dict):
        raise RuntimeError("Yahoo STRC 无 result")
    meta = res.get("meta") or {}
    price = float(meta.get("regularMarketPrice") or 0)
    if price <= 0:
        raise RuntimeError("Yahoo STRC 无有效价")
    ts = res.get("timestamp") or []
    closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    days: list[dict[str, Any]] = []
    for t, c in zip(ts, closes):
        if c is None:
            continue
        days.append(
            {
                "day": datetime.fromtimestamp(int(t), timezone.utc).strftime("%Y-%m-%d"),
                "close": round(float(c), 4),
            }
        )
    return {
        "price": round(price, 4),
        "currency": meta.get("currency") or "USD",
        "below_par": price < PAR,
        "vs_par_pct": round((price / PAR - 1) * 100, 2),
        "days": days,
    }


def fetch_apx() -> dict[str, Any]:
    doc = http_json(LLAMA)
    tokens = doc.get("tokens") if isinstance(doc, dict) else None
    if not isinstance(tokens, list) or not tokens:
        raise RuntimeError("DefiLlama apxUSD 无 tokens")
    series: list[dict[str, Any]] = []
    for row in tokens:
        if not isinstance(row, dict):
            continue
        circ = (row.get("circulating") or {}).get("peggedUSD")
        if circ is None:
            continue
        day = datetime.fromtimestamp(int(row["date"]), timezone.utc).strftime("%Y-%m-%d")
        series.append({"day": day, "mcap_usd": float(circ)})
    if not series:
        raise RuntimeError("apxUSD 序列空")
    series.sort(key=lambda x: x["day"])
    latest = series[-1]
    ath = max(series, key=lambda x: x["mcap_usd"])
    target = datetime.strptime(latest["day"], "%Y-%m-%d").date() - timedelta(days=7)
    week = min(
        series,
        key=lambda x: abs(datetime.strptime(x["day"], "%Y-%m-%d").date() - target),
    )
    mcap = float(latest["mcap_usd"])
    week_mcap = float(week["mcap_usd"])
    ath_mcap = float(ath["mcap_usd"])
    return {
        "id": 354,
        "name": doc.get("name") or "apxUSD",
        "symbol": doc.get("symbol") or "apxUSD",
        "mcap_usd": mcap,
        "asof_day": latest["day"],
        "ath_mcap_usd": ath_mcap,
        "ath_day": ath["day"],
        "drawdown_from_ath_pct": round((mcap / ath_mcap - 1) * 100, 1) if ath_mcap else None,
        "week_ago_day": week["day"],
        "week_ago_mcap_usd": week_mcap,
        "chg_7d_usd": round(mcap - week_mcap, 2),
        "chg_7d_pct": round((mcap / week_mcap - 1) * 100, 1) if week_mcap else None,
        "series": series[-120:],
    }


def main() -> int:
    strc = fetch_strc()
    apx = fetch_apx()
    payload = {
        "updated_at": now_iso(),
        "source": "Yahoo STRC + DefiLlama stablecoin/354 apxUSD",
        "note": "非 Dune Gate Research；数字可能与原文略有差异。",
        "par_usd": PAR,
        "strc": strc,
        "apxusd": apx,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(
        f"wrote {OUT_PATH} STRC={strc['price']} below_par={strc['below_par']} "
        f"apx={apx['mcap_usd']:.0f} dd={apx['drawdown_from_ath_pct']}% "
        f"wow={apx['chg_7d_usd']:.0f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
