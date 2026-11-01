#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BNB 现货 24h 涨跌过线 → Lark 新闻。

过线：滚动 24 小时 |涨跌| ≥ 10%（BNB_MOVE_PCT 可改）。
只发最新过线新闻体，不发日常流水，不回扫历史，不写 Ondo/产品叙事。
优先 Binance 公开 ticker，失败再 CoinGecko。

用法：
  python monitor.py
  python monitor.py --push
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
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_PATH = HERE / "output" / "json" / "bnb_spot.json"
STATE_PATH = HERE / "output" / "lark_state.json"
LOG_PATH = HERE / "monitor.log"
JSON_URL = "https://data.wublock123.com/dashboard/exchange_volume"
PAGE = "https://data.wublock123.com/dashboard/exchange_volume"
BINANCE_URL = "https://api.binance.com/api/v3/ticker/24hr?symbol=BNBUSDT"
UA = "dashboard-data/bnb-spot"
TZ_CN = timezone(timedelta(hours=8))
DEFAULT_MOVE_PCT = 10.0
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

sys.path.insert(0, str(ROOT))
from lark_copy import as_news  # noqa: E402


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
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET", "BNB_MOVE_PCT",
                "COINGECKO_KEY", "COINGECKO_DEMO_API_KEY"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def gen_lark_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark_text(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = gen_lark_sign(secret, ts)
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    if body:
        result = json.loads(body)
        if str(result.get("code", "0")) != "0":
            raise RuntimeError(f"Lark 返回异常: {result}")


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": []}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": []}
    ids = doc.get("sentIds") if isinstance(doc, dict) else None
    return {"sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else []}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = now_iso()
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def append_log(stamp: str, msg_id: str, ok: bool, text: str) -> None:
    line = json.dumps({"ts": stamp, "id": msg_id, "ok": ok, "text": text}, ensure_ascii=False)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def http_json(url: str, headers: dict[str, str] | None = None) -> Any:
    req = urllib.request.Request(
        url,
        headers={**(headers or {}), "User-Agent": UA, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def num(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def fetch_binance() -> dict[str, Any]:
    row = http_json(BINANCE_URL)
    if not isinstance(row, dict):
        raise RuntimeError("Binance 返回异常")
    price = num(row.get("lastPrice"))
    chg = num(row.get("priceChangePercent"))
    vol = num(row.get("quoteVolume"))
    if price <= 0:
        raise RuntimeError("Binance 无有效价")
    return {
        "source": "binance",
        "price": round(price, 4),
        "chg_24h_pct": round(chg, 4),
        "volume_24h_usd": round(vol, 2),
        "high_24h": round(num(row.get("highPrice")), 4) or None,
        "low_24h": round(num(row.get("lowPrice")), 4) or None,
        "market_cap_usd": None,
    }


def fetch_coingecko(env: dict[str, str]) -> dict[str, Any]:
    key = (env.get("COINGECKO_KEY") or env.get("COINGECKO_DEMO_API_KEY") or "").strip()
    qs = urllib.parse.urlencode({"vs_currency": "usd", "ids": "binancecoin"})
    attempts: list[tuple[str, dict[str, str]]] = []
    if key:
        attempts.append(
            (f"https://api.coingecko.com/api/v3/coins/markets?{qs}", {"x-cg-demo-api-key": key})
        )
        attempts.append(
            (f"https://pro-api.coingecko.com/api/v3/coins/markets?{qs}", {"x-cg-pro-api-key": key})
        )
    attempts.append((f"https://api.coingecko.com/api/v3/coins/markets?{qs}", {}))
    last: Exception | None = None
    rows: Any = None
    for url, headers in attempts:
        try:
            rows = http_json(url, headers)
            break
        except Exception as exc:  # noqa: BLE001
            last = exc
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        raise RuntimeError(f"CoinGecko 失败: {last}")
    row = rows[0]
    price = num(row.get("current_price"))
    if price <= 0:
        raise RuntimeError("CoinGecko 无有效价")
    return {
        "source": "coingecko",
        "price": round(price, 4),
        "chg_24h_pct": round(num(row.get("price_change_percentage_24h")), 4),
        "volume_24h_usd": round(num(row.get("total_volume")), 2),
        "high_24h": round(num(row.get("high_24h")), 4) or None,
        "low_24h": round(num(row.get("low_24h")), 4) or None,
        "market_cap_usd": round(num(row.get("market_cap")), 2) or None,
    }


def fetch_live(env: dict[str, str]) -> dict[str, Any]:
    try:
        return fetch_binance()
    except Exception as binance_exc:  # noqa: BLE001
        print(f"[warn] Binance: {binance_exc}")
        live = fetch_coingecko(env)
        live["fallback_from"] = "binance_failed"
        return live


def yi(usd: float | None) -> str:
    if usd is None or usd <= 0:
        return "—"
    if usd >= 1e8:
        return f"{usd / 1e8:.2f} 亿美元"
    if usd >= 1e4:
        return f"{usd / 1e4:.0f} 万美元"
    return f"{usd:.0f} 美元"


def pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def build_news(live: dict[str, Any]) -> str:
    now = datetime.now(TZ_CN)
    stamp = f"{now.year} 年 {now.month} 月 {now.day} 日 {now.hour:02d}:{now.minute:02d}"
    src = "Binance" if live.get("source") == "binance" else "CoinGecko"
    mcap_bit = ""
    if live.get("market_cap_usd"):
        mcap_bit = f"市值约 {yi(float(live['market_cap_usd']))}。"
    range_bit = ""
    if live.get("high_24h") and live.get("low_24h"):
        range_bit = (
            f"过去 24 小时最高约 {float(live['high_24h']):.2f} 美元、"
            f"最低约 {float(live['low_24h']):.2f} 美元。"
        )
    text = (
        f"吴说获悉，据吴说数据中心统计，截至 {stamp}（UTC+8），"
        f"{src} 口径 BNB 现报约 {float(live['price']):.2f} 美元，"
        f"过去 24 小时约 {pct(float(live['chg_24h_pct']))}，"
        f"成交额约 {yi(float(live.get('volume_24h_usd') or 0))}。"
        f"{mcap_bit}{range_bit}"
        f"\n{PAGE}"
    )
    return as_news(text)


def save_snapshot(live: dict[str, Any], over: bool, move_pct: float) -> None:
    payload = {
        "updated_at": now_iso(),
        "source": live.get("source"),
        "move_pct": move_pct,
        "note": "滚动 24h |涨跌| 达到阈值才发新闻；不发日常流水；不回扫；不含 Ondo。",
        "live": live,
        "over_threshold": over,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    env = load_env()
    move_pct = float(env.get("BNB_MOVE_PCT") or os.environ.get("BNB_MOVE_PCT") or DEFAULT_MOVE_PCT)

    try:
        live = fetch_live(env)
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] 拉行情失败: {exc}", file=sys.stderr)
        return 1

    chg = float(live["chg_24h_pct"])
    over = abs(chg) >= move_pct
    save_snapshot(live, over, move_pct)
    print(
        f"  source={live.get('source')} price={live['price']} "
        f"chg24={chg}% vol={live.get('volume_24h_usd')}"
    )
    if not over:
        print(f"[skip] |24h| 未到 {move_pct:g}%")
        return 0

    day = datetime.now(TZ_CN).strftime("%Y-%m-%d")
    direction = "up" if chg >= 0 else "down"
    msg_id = f"bnb-move-{direction}-{day}"
    text = build_news(live)
    state = load_state()
    print("---")
    print(f"id={msg_id} chg={chg}% already={msg_id in state['sentIds']}")
    print(text)
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0

    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
        return 0
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    try:
        send_lark_text(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Lark 失败: {exc}", file=sys.stderr)
        append_log(stamp, msg_id, False, text)
        return 1
    print(f"[pushed] {msg_id}")
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-200:]
    save_state(state)
    append_log(stamp, msg_id, True, text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
