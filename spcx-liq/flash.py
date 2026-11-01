#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SPCX 永续爆仓与价位 → Lark 新闻体。

价格用币安 U 本位 SPCXUSDT。爆仓与排名用已有 CoinGlass coin-list 24h。
不占 Dune。只发最新一条：24 小时最低价跌破 150 或 135，或爆仓达到 5000 万美元，
或爆仓排名进入前三。同一天只发一次。不回扫旧窗口。
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
OUT_PATH = HERE / "output" / "json" / "spcx_liq.json"
STATE_PATH = HERE / "output" / "spcx_liq_lark.json"
LOG_PATH = HERE / "flash.log"
JSON_URL = "https://data.wublock123.com/dashboard/tradfi"
PAGE = "https://data.wublock123.com/dashboard/tradfi"
LIQ_URL = "https://open-api-v4.coinglass.com/api/futures/liquidation/coin-list?range=24h"
TICKER_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=SPCXUSDT"
LEVELS = (150.0, 135.0)
LIQ_USD = 50_000_000.0
LIQ_RANK = 3
TZ_CN = timezone(timedelta(hours=8))
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

sys.path.insert(0, str(ROOT / "coinglass-history"))
import _common as C  # noqa: E402

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
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def http_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "dashboard-data/spcx-liq", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def liq_usd(row: dict[str, Any]) -> float:
    return float(row.get("liquidation_usd_24h") or row.get("total_liquidation_usd") or 0)


def fetch_live() -> dict[str, Any]:
    status, payload = C.http_get_json(LIQ_URL, retries=1)
    rows = payload.get("data") if isinstance(payload, dict) else None
    if status != 200 or not isinstance(rows, list):
        raise RuntimeError(f"CoinGlass coin-list {status}")
    ranked = sorted((row for row in rows if isinstance(row, dict)), key=liq_usd, reverse=True)
    spcx = next((row for row in ranked if str(row.get("symbol") or "").upper() == "SPCX"), None)
    if spcx is None:
        raise RuntimeError("CoinGlass 没有 SPCX")
    ticker = http_json(TICKER_URL)
    if not isinstance(ticker, dict) or not ticker.get("lastPrice"):
        raise RuntimeError("币安 SPCXUSDT 行情缺失")
    return {
        "asof": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "price": float(ticker["lastPrice"]),
        "low_24h": float(ticker["lowPrice"]),
        "high_24h": float(ticker["highPrice"]),
        "chg_24h_pct": float(ticker.get("priceChangePercent") or 0),
        "liquidation_usd_24h": liq_usd(spcx),
        "long_liquidation_usd_24h": float(spcx.get("long_liquidation_usd_24h") or 0),
        "short_liquidation_usd_24h": float(spcx.get("short_liquidation_usd_24h") or 0),
        "liquidation_rank_24h": ranked.index(spcx) + 1,
        "liquidation_top3": [str(row.get("symbol") or "") for row in ranked[:3]],
    }


def save_doc(live: dict[str, Any]) -> None:
    payload = {
        "updated_at": live["asof"],
        "source": "Binance fapi SPCXUSDT 24hr + CoinGlass liquidation coin-list 24h",
        "note": "价位 150 与 135。爆仓 5000 万美元或排名前三才写入触发。非纳斯达克现货。",
        "live": live,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)


def usd_cn(value: float) -> str:
    if value >= 1e8:
        return f"{value / 1e8:.2f} 亿美元"
    if value >= 1e4:
        return f"{value / 1e4:.0f} 万美元"
    return f"{value:.0f} 美元"


def px(value: float) -> str:
    return f"{value:.2f}"


def reasons(live: dict[str, Any]) -> list[str]:
    out = []
    low = float(live["low_24h"])
    for level in LEVELS:
        if low < level:
            out.append(f"below-{int(level)}")
            break
    if float(live["liquidation_usd_24h"]) >= LIQ_USD:
        out.append("liq")
    if int(live["liquidation_rank_24h"]) <= LIQ_RANK:
        out.append("rank")
    return out


def build_news(live: dict[str, Any], hits: list[str]) -> str:
    now = datetime.now(TZ_CN)
    stamp = f"{now.month} 月 {now.day} 日 {now.hour:02d}:{now.minute:02d}"
    low = float(live["low_24h"])
    level_bit = ""
    if "below-135" in hits:
        level_bit = f"过去 24 小时最低约 {px(low)} 美元，已低于 135 美元。"
    elif "below-150" in hits:
        level_bit = f"过去 24 小时最低约 {px(low)} 美元，低于 150 美元，仍高于 135 美元。"
    else:
        level_bit = f"过去 24 小时最低约 {px(low)} 美元，最高约 {px(float(live['high_24h']))} 美元。"
    chg = float(live["chg_24h_pct"])
    word = "涨幅" if chg >= 0 else "跌幅"
    liq = float(live["liquidation_usd_24h"])
    rank = int(live["liquidation_rank_24h"])
    top = "、".join(live.get("liquidation_top3") or [])
    rank_bit = f"排名第 {rank}"
    if "rank" in hits and top:
        rank_bit += f"，前三为 {top}"
    return (
        f"据吴说数据中心统计，截至 {stamp}（UTC+8），SpaceX 相关永续 SPCX 现报 {px(float(live['price']))} 美元，"
        f"24 小时{word}约 {abs(chg):.2f}%。{level_bit}"
        f"CoinGlass 近 24 小时 SPCX 爆仓约 {usd_cn(liq)}"
        f"（多单约 {usd_cn(float(live['long_liquidation_usd_24h']))}，"
        f"空单约 {usd_cn(float(live['short_liquidation_usd_24h']))}），{rank_bit}。"
        f"\n{PAGE}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        live = fetch_live()
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError, ValueError, KeyError) as exc:
        print(f"[ERROR] 拉数失败: {exc}", file=sys.stderr)
        return 1
    save_doc(live)
    hits = reasons(live)
    print(
        f"[spcx-liq] px={live['price']} low={live['low_24h']} "
        f"liq={live['liquidation_usd_24h']:.0f} rank={live['liquidation_rank_24h']} hits={hits or 'none'}"
    )
    if not hits and not args.force:
        print("[skip] 未跌破 150/135，爆仓也未进前三或达到 5000 万美元")
        return 0
    day = datetime.now(TZ_CN).strftime("%Y-%m-%d")
    msg_id = f"spcx-liq-{day}"
    text = build_news(live, hits)
    print("---")
    print(f"id={msg_id}")
    print(as_news(text))
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0
    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    state = load_state()
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
        return 0
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
