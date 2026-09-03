#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 etf-history 重建 etf-premium-discount-history。

CoinGlass 的 /api/etf/bitcoin/premium-discount/history 自 2026-05-15 起
内容不再前进（端点仍 200，只是窗口冻住）。同套餐的 /history 却还在每天
给每只 ETF 写 market_price / nav / premium_discount。首页「均溢价」tile、
ETF 页折溢价图、报告检测器都读 premium-discount-history 那一份，
所以用 history 按现货 ETF 重建成原来的信封，下游不用改路径。

只覆盖 bitcoin：Coinglass v4 的 /etf/{market}/history 对 ethereum 是 404。
写入 etf-premium-discount-history/output/json/bitcoin.json，
必须排在该模块自己的 csv_to_json 之后（run_all 里 etf-history 在它后面），
否则会被冻结的上游窗口再盖回去。

最新一天用「各 ticker 最近一次观测、且落在全局最大日期 4 天内」拼快照，
避免周五部分文件停在周四、周日看起来像只有 4 只小盘在更新。
历史序列仍按交易日对当天有数的 ticker 做等权平均，给 sparkline 用。
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import _common as C  # noqa: E402

HERE = C.module_dir(__file__)
CACHE_DIR = HERE / "cache"
LIST_CACHE = HERE.parent / "etf-list" / "cache"
PREMIUM_OUT_DIR = HERE.parent / "etf-premium-discount-history" / "output" / "json"

# 现货 BTC ETF。etf-list 的 fund_type=Spot 是权威来源；这份兜底防止 list 缺失。
FALLBACK_SPOT = frozenset({
    "IBIT", "FBTC", "GBTC", "BITB", "ARKB", "HODL", "EZBC", "BTCO", "BRRR", "BTCW", "BTC",
})
HEADLINE_WINDOW_DAYS = 4
MIN_POINTS = 100


def load_spot_tickers(market: str) -> set[str]:
    path = LIST_CACHE / f"{market}.json"
    if path.is_file():
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            items = []
        if isinstance(items, list):
            out = {
                str(it["ticker"]).upper()
                for it in items
                if isinstance(it, dict)
                and it.get("ticker")
                and str(it.get("fund_type") or "").lower() == "spot"
            }
            if out:
                return out
    return set(FALLBACK_SPOT) if market == "bitcoin" else set()


def premium_of(row: dict[str, Any]) -> float | None:
    extra_raw = row.get("extra_json") or ""
    extra: dict[str, Any] = {}
    if extra_raw:
        try:
            parsed = json.loads(extra_raw)
            if isinstance(parsed, dict):
                extra = parsed
        except json.JSONDecodeError:
            pass
    raw = extra.get("premium_discount")
    if raw not in (None, ""):
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = None
        else:
            if value == value:  # not NaN
                return value
    nav = row.get("open")
    price = row.get("close")
    try:
        nav_f = float(nav) if nav not in (None, "") else 0.0
        price_f = float(price) if price not in (None, "") else 0.0
    except (TypeError, ValueError):
        return None
    if nav_f > 0 and price_f > 0:
        return (price_f / nav_f - 1.0) * 100.0
    return None


def nav_price(row: dict[str, Any]) -> tuple[float | None, float | None]:
    nav = row.get("open")
    price = row.get("close")
    try:
        nav_f = float(nav) if nav not in (None, "") else None
    except (TypeError, ValueError):
        nav_f = None
    try:
        price_f = float(price) if price not in (None, "") else None
    except (TypeError, ValueError):
        price_f = None
    return nav_f, price_f


def load_ticker_points(market: str, ticker: str) -> list[dict[str, Any]]:
    cache = CACHE_DIR / f"{market}_{ticker}.csv"
    if not cache.is_file():
        return []
    rows = C.read_ohlc_cache(cache)
    out: list[dict[str, Any]] = []
    for ts in sorted(rows):
        row = rows[ts]
        prem = premium_of(row)
        if prem is None:
            continue
        nav, price = nav_price(row)
        out.append({
            "time": int(ts),
            "ticker": ticker,
            "premium_discount_details": prem,
            "nav_usd": nav,
            "market_price_usd": price,
        })
    return out


def write_envelope(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def build_market(market: str) -> dict[str, Any] | None:
    tickers = sorted(load_spot_tickers(market))
    if not tickers:
        print(f"  [{market}] no spot tickers, skip")
        return None

    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for ticker in tickers:
        points = load_ticker_points(market, ticker)
        if points:
            by_ticker[ticker] = points
    if not by_ticker:
        print(f"  [{market}] no premium points, skip")
        return None

    by_day: dict[int, list[dict[str, Any]]] = defaultdict(list)
    last_of: dict[str, dict[str, Any]] = {}
    for ticker, points in by_ticker.items():
        last_of[ticker] = points[-1]
        for pt in points:
            by_day[pt["time"]].append(pt)

    days: list[dict[str, Any]] = []
    for ts in sorted(by_day):
        items = by_day[ts]
        days.append({
            "timestamp": ts,
            "list": [
                {
                    "ticker": it["ticker"],
                    "market_price_usd": it["market_price_usd"],
                    "nav_usd": it["nav_usd"],
                    "premium_discount_details": round(it["premium_discount_details"], 6),
                }
                for it in sorted(items, key=lambda x: x["ticker"])
            ],
        })

    max_ts = max(pt["time"] for pt in last_of.values())
    window_start = max_ts - HEADLINE_WINDOW_DAYS * 24 * 3600 * 1000
    headline_list = [
        pt for pt in last_of.values() if pt["time"] >= window_start
    ]
    if headline_list:
        # 用窗口内各 ticker 最新一行替换「最后交易日」那一条，避免周日只剩 4 只小盘。
        headline_ts = max(pt["time"] for pt in headline_list)
        days = [d for d in days if d["timestamp"] < headline_ts] + [{
            "timestamp": headline_ts,
            "list": [
                {
                    "ticker": it["ticker"],
                    "market_price_usd": it["market_price_usd"],
                    "nav_usd": it["nav_usd"],
                    "premium_discount_details": round(it["premium_discount_details"], 6),
                }
                for it in sorted(headline_list, key=lambda x: x["ticker"])
            ],
        }]

    if len(days) < MIN_POINTS:
        print(f"  [{market}] only {len(days)} days (< {MIN_POINTS}), refuse to overwrite")
        return None

    last = days[-1]["list"]
    avg = sum(x["premium_discount_details"] for x in last) / len(last)
    last_day = datetime.fromtimestamp(days[-1]["timestamp"] / 1000, tz=timezone.utc).date()
    print(
        f"  [{market}] days={len(days)} last={last_day} n={len(last)} "
        f"avg={avg:.4f}% tickers={','.join(x['ticker'] for x in last)}"
    )
    return {
        "code": "0",
        "msg": "success",
        "source": "derived from etf-history (spot tickers)",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data": days,
    }


def main() -> None:
    print("[aggregate_premium] rebuild etf-premium-discount-history from etf-history")
    payload = build_market("bitcoin")
    if payload is None:
        raise SystemExit("[FATAL] bitcoin premium aggregate failed")
    out = PREMIUM_OUT_DIR / "bitcoin.json"
    write_envelope(out, payload)
    print(f"  wrote {out}")


if __name__ == "__main__":
    main()
