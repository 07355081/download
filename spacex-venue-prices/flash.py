#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SpaceX 相关永续各所免费报价 → JSON。价差创序列新高才发新闻。

可比口径：OKX / Bitget / Gate / 币安 / Hyperliquid 的 SPCX 永续。
PreStocks 等代币化近邻单独列出，不参与价差。
只在价差（最高/最低 − 1）创已存序列新高，且不低于 MIN_SPREAD_PCT 时发。
第一次 --push 只建基线。同一上海自然日最多一条。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_PATH = HERE / "output" / "json" / "spacex_venue_prices.json"
STATE_PATH = HERE / "output" / "spacex_venue_prices_lark.json"
LOG_PATH = HERE / "flash.log"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/spacex-venue-prices"
LIQ_CANDIDATES = [
    Path("/root/dashboard/public/json/spacex-liquidity/spacex_liquidity.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "spacex-liquidity" / "spacex_liquidity.json",
    ROOT / "spacex-liquidity" / "output" / "json" / "spacex_liquidity.json",
]
MIN_SPREAD_PCT = 8.0
KEEP_DAYS = 180
TZ_CN = timezone(timedelta(hours=8))
UA = "dashboard-data/spacex-venue-prices"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

sys.path.insert(0, str(ROOT))
from lark_copy import as_news  # noqa: E402


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
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def gen_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": as_news(text)}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = gen_sign(secret, ts)
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode("utf-8") or "{}")
    if str(body.get("code", "0")) != "0":
        raise RuntimeError(body)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": [], "bootstrapped": False}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": [], "bootstrapped": False}
    if not isinstance(doc, dict):
        return {"sentIds": [], "bootstrapped": False}
    ids = doc.get("sentIds")
    return {
        "sentIds": [item for item in ids if isinstance(item, str)] if isinstance(ids, list) else [],
        "bootstrapped": bool(doc.get("bootstrapped")),
    }


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def http_json(url: str, body: dict[str, Any] | None = None, timeout: int = 25) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    last: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            if attempt == 2:
                raise
            time.sleep(1.2 + attempt)
    raise RuntimeError(last)


def num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out <= 0:
        return None
    return out


def fetch_okx() -> dict[str, Any] | None:
    try:
        doc = http_json("https://www.okx.com/api/v5/market/ticker?instId=SPCX-USDT-SWAP")
        rows = doc.get("data") if isinstance(doc, dict) else None
        if not isinstance(rows, list) or not rows:
            return None
        price = num(rows[0].get("last"))
        if price is None:
            return None
        return {"name": "OKX", "symbol": "SPCX-USDT-SWAP", "price": price, "group": "perp"}
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] okx: {exc}")
        return None


def fetch_bitget() -> dict[str, Any] | None:
    try:
        doc = http_json(
            "https://api.bitget.com/api/v2/mix/market/ticker?productType=USDT-FUTURES&symbol=SPCXUSDT"
        )
        rows = doc.get("data") if isinstance(doc, dict) else None
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list) or not rows:
            return None
        price = num(rows[0].get("lastPr") or rows[0].get("last"))
        if price is None:
            return None
        return {"name": "Bitget", "symbol": "SPCXUSDT", "price": price, "group": "perp"}
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] bitget: {exc}")
        return None


def fetch_gate() -> dict[str, Any] | None:
    try:
        rows = http_json("https://api.gateio.ws/api/v4/futures/usdt/tickers?contract=SPCX_USDT")
        if not isinstance(rows, list) or not rows:
            return None
        price = num(rows[0].get("last"))
        if price is None:
            return None
        return {"name": "Gate", "symbol": "SPCX_USDT", "price": price, "group": "perp"}
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] gate: {exc}")
        return None


def fetch_binance() -> dict[str, Any] | None:
    try:
        doc = http_json("https://fapi.binance.com/fapi/v1/ticker/price?symbol=SPCXUSDT")
        price = num(doc.get("price") if isinstance(doc, dict) else None)
        if price is None:
            raise RuntimeError("empty")
        return {"name": "Binance", "symbol": "SPCXUSDT", "price": price, "group": "perp"}
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] binance: {exc}")
        for path in LIQ_CANDIDATES:
            if not path.is_file():
                continue
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                mid = num(((doc.get("live") or {}).get("binance") or {}).get("mid"))
                if mid is not None:
                    return {"name": "Binance", "symbol": "SPCXUSDT", "price": mid, "group": "perp", "note": "liquidity mid"}
            except (OSError, json.JSONDecodeError, TypeError):
                continue
        return None


def fetch_hyperliquid() -> dict[str, Any] | None:
    try:
        book = http_json("https://api.hyperliquid.xyz/info", {"type": "l2Book", "coin": "xyz:SPCX"})
        levels = book.get("levels") if isinstance(book, dict) else None
        if not isinstance(levels, list) or len(levels) < 2:
            raise RuntimeError("no book")
        bids, asks = levels[0], levels[1]
        bid = num((bids[0] or {}).get("px")) if isinstance(bids, list) and bids else None
        ask = num((asks[0] or {}).get("px")) if isinstance(asks, list) and asks else None
        if bid is None or ask is None:
            raise RuntimeError("no top")
        return {"name": "Hyperliquid", "symbol": "xyz:SPCX", "price": (bid + ask) / 2, "group": "perp"}
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] hyperliquid: {exc}")
        for path in LIQ_CANDIDATES:
            if not path.is_file():
                continue
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                mid = num(((doc.get("live") or {}).get("hyperliquid") or {}).get("mid"))
                if mid is not None:
                    return {
                        "name": "Hyperliquid",
                        "symbol": "xyz:SPCX",
                        "price": mid,
                        "group": "perp",
                        "note": "liquidity mid",
                    }
            except (OSError, json.JSONDecodeError, TypeError):
                continue
        return None


def fetch_prestocks() -> dict[str, Any] | None:
    try:
        doc = http_json(
            "https://api.coingecko.com/api/v3/simple/price?ids=spacex-prestocks-2&vs_currencies=usd"
        )
        price = num(((doc.get("spacex-prestocks-2") or {}) if isinstance(doc, dict) else {}).get("usd"))
        if price is None:
            return None
        return {"name": "PreStocks", "symbol": "SPACEX", "price": price, "group": "tokenized"}
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] prestocks: {exc}")
        return None


def load_history() -> list[dict[str, Any]]:
    if not OUT_PATH.is_file():
        return []
    try:
        doc = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = doc.get("days") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = str(row.get("day") or "")
        value = num(row.get("value") if row.get("value") is not None else row.get("spread_pct"))
        if len(day) == 10 and value is not None:
            out.append({"day": day, "value": value, "spread_pct": value})
    return out


def cn_now() -> str:
    return datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")


def cn_day(day: str) -> str:
    year, month, dom = day.split("-")
    return f"{int(year)}年{int(month)}月{int(dom)}日"


def build_news(day: str, venues: list[dict[str, Any]], spread: float, hi: dict[str, Any], lo: dict[str, Any]) -> str:
    bits = [
        f"吴说获悉，据吴说数据中心统计，截至 {cn_now()}（UTC+8），SpaceX 相关永续各所报价出现明显分歧。"
        f"最高为 {hi['name']} 约 {hi['price']:.2f} 美元，最低为 {lo['name']} 约 {lo['price']:.2f} 美元，"
        f"价差约 {spread:.1f}%，创本序列新高。"
    ]
    ordered = sorted(venues, key=lambda row: float(row["price"]), reverse=True)
    detail = "；".join(f"{row['name']} 约 {float(row['price']):.2f} 美元" for row in ordered)
    bits.append(f"各所报价：{detail}。")
    bits.append("口径为各所公开永续行情，标的名称可能不同，不宜当成同一可交割资产。")
    return "".join(bits) + f"\n{PAGE}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    venues = [row for row in (fetch_okx(), fetch_bitget(), fetch_gate(), fetch_binance(), fetch_hyperliquid()) if row]
    extras = [row for row in (fetch_prestocks(),) if row]
    if len(venues) < 2:
        print("[ERROR] 可比所不足", file=sys.stderr)
        return 1

    prices = [float(row["price"]) for row in venues]
    hi_price = max(prices)
    lo_price = min(prices)
    spread = (hi_price / lo_price - 1.0) * 100.0
    hi = next(row for row in venues if float(row["price"]) == hi_price)
    lo = next(row for row in venues if float(row["price"]) == lo_price)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    history = load_history()
    history = [row for row in history if row["day"] != day]
    history.append({"day": day, "value": round(spread, 3), "spread_pct": round(spread, 3)})
    history.sort(key=lambda row: row["day"])
    history = history[-KEEP_DAYS:]

    doc = {
        "name": "SpaceX 相关永续各所报价",
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "as_of": datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S"),
        "day": day,
        "spread_pct": round(spread, 3),
        "max_venue": hi["name"],
        "min_venue": lo["name"],
        "venues": venues,
        "extras": extras,
        "days": history,
        "latest_top": [
            {"symbol": row["name"], "market_cap": round(float(row["price"]), 4)} for row in sorted(venues, key=lambda r: -float(r["price"]))
        ],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(
        f"[spacex-venue-prices] n={len(venues)} spread={spread:.2f}% "
        f"hi={hi['name']} {hi_price:.2f} lo={lo['name']} {lo_price:.2f}"
    )

    prior = [float(row["value"]) for row in history[:-1]]
    ath = bool(prior) and spread > max(prior) + 0.05
    if not prior:
        ath = spread >= MIN_SPREAD_PCT
    big_enough = spread >= MIN_SPREAD_PCT
    print(f"[spacex-venue-prices] ath={ath} big_enough={big_enough} prior_n={len(prior)}")

    state = load_state()
    first = False
    if args.push and not state["bootstrapped"] and not args.force:
        state["bootstrapped"] = True
        state["baselineDay"] = day
        state["baselineSpread"] = round(spread, 3)
        save_state(state)
        first = True
        print(f"[bootstrap] 已记基线 day={day} spread={spread:.2f}%")

    if not ath or not big_enough:
        if not args.force:
            print("[skip] 未创价差新高，或未达到阈值")
            return 0

    text = build_news(day, venues, spread, hi, lo)
    msg_id = f"spacex-venue-prices-{day}"
    print("---")
    print(f"id={msg_id}")
    print(as_news(text))
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0
    if first:
        print("[bootstrap] 不发已有价差")
        return 0
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
        return 0
    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    try:
        send_lark(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Lark 失败: {exc}", file=sys.stderr)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"=== {stamp} {msg_id} ok=False {exc}\n{text}\n\n")
        return 1
    print(f"[pushed] {msg_id}")
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-200:]
    save_state(state)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} {msg_id} ok=True\n{text}\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
