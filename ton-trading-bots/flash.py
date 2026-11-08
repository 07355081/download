#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TON 交易机器人 → Lark 新闻体。

过线（只发最新，不回扫）：
- 合计日成交（TON）创本通道新高
- 或 |日环比| ≥ 40%
- 或 交易者加总创本通道新高

首日建基线不推；缓存 stale 不推。
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
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    HERE / "output" / "json" / "ton_trading_bots.json",
    Path("/root/dashboard/public/json/ton-trading-bots/ton_trading_bots.json"),
]
STATE_PATH = HERE / "output" / "ton_trading_bots_lark.json"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/ton-trading-bots"
SOURCE = "https://dune.com/xrocket_tg/trading-bots-on-ton"
DOD_MIN = 40.0
TZ_CN = timezone(timedelta(hours=8))
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


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": as_news(text)}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(
            hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
        ).decode("utf-8")
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


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def save_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_doc() -> dict[str, Any]:
    for path in JSON_CANDIDATES:
        if path.is_file():
            doc = load_json(path)
            if doc:
                return doc
    raise FileNotFoundError("找不到 ton_trading_bots.json")


def fmt_ton(n: float) -> str:
    if n >= 1e4:
        return f"{n / 1e4:.2f} 万 TON"
    return f"{n:,.0f} TON"


def yi_usd(n: float | None) -> str:
    if n is None:
        return "—"
    if abs(n) >= 1e8:
        return f"{n / 1e8:.2f} 亿美元"
    return f"{n / 1e4:.0f} 万美元"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()

    doc = load_doc()
    state = load_json(STATE_PATH)
    sent = set(state.get("sentIds") or [])
    day = str(doc.get("day") or "")
    if not day:
        print("[skip] no day")
        return 0
    if doc.get("stale"):
        print(f"[skip] stale age={doc.get('cache_age_hours')}")
        return 0

    vol = float(doc.get("volume_ton") or 0)
    traders = int(doc.get("traders_sum") or 0)
    dod = doc.get("dod_pct")
    top = doc.get("top_bots") if isinstance(doc.get("top_bots"), list) else []

    if not state.get("baseline"):
        state = {
            "baseline": True,
            "sentIds": list(sent),
            "peak_vol": vol,
            "peak_traders": traders,
        }
        save_json(STATE_PATH, state)
        print(f"[baseline] day={day} vol={vol} traders={traders}")
        return 0

    reasons: list[str] = []
    tags: list[str] = []
    peak_vol = float(state.get("peak_vol") or 0)
    peak_traders = int(state.get("peak_traders") or 0)

    if vol > peak_vol + 1e-6:
        reasons.append(f"TON 交易机器人合计日成交创本通道新高，约 {fmt_ton(vol)}")
        tags.append("volAth")
        state["peak_vol"] = vol
    if isinstance(dod, (int, float)) and abs(float(dod)) >= DOD_MIN:
        reasons.append(f"合计日成交较前一日约 {float(dod):+.1f}%")
        tags.append("volDod")
    if traders > peak_traders:
        reasons.append(f"各机器人当日交易者加总创本通道新高，约 {traders:,}")
        tags.append("tradersAth")
        state["peak_traders"] = traders

    if not reasons:
        save_json(STATE_PATH, state)
        print(f"[skip] day={day} no threshold")
        return 0

    msg_id = f"ton-bots-{day}-{'-'.join(tags)}"
    if msg_id in sent:
        print(f"[skip] already {msg_id}")
        return 0

    top_txt = "、".join(
        f"{b.get('bot')}约 {fmt_ton(float(b.get('volume_ton') or 0))}" for b in top[:3]
    )
    now = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
    text = (
        f"吴说获悉，据吴说数据中心统计（Dune xRocket Trading Bots on TON 缓存），"
        f"截至 {now}（UTC+8），{'；'.join(reasons)}。"
        f"{day} 合计成交约 {fmt_ton(vol)}"
        + (f"（约 {yi_usd(doc.get('volume_usd'))}）" if doc.get("volume_usd") else "")
        + f"，交易者加总约 {traders:,}，前三为 {top_txt}。"
        "交易者按机器人加总可能重复；不回扫历史日。"
        f"\n{PAGE}\n源 {SOURCE}"
    )
    print(msg_id)
    print(text)
    if args.push:
        env = load_env()
        webhook = env.get("LARK_ALERT_WEBHOOK")
        if not webhook:
            raise SystemExit("缺少 LARK_ALERT_WEBHOOK")
        send_lark(webhook, env.get("LARK_ALERT_SECRET"), text)
        sent.add(msg_id)
        state["sentIds"] = list(sent)[-200:]
        print(f"[pushed] {msg_id}")
    save_json(STATE_PATH, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
