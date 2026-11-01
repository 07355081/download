# -*- coding: utf-8 -*-
"""Bitfinex 保证金多头创 2023 年 12 月以来新高 → 一条可发稿新闻。

只发最新读数。没创新高不发。同一高点不重复发。不回扫 5 月旧窗口。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    HERE / "output" / "json" / "longs.json",
    Path("/root/dashboard/public/json/bitfinex-btc-longs/longs.json"),
]
STATE_PATH = HERE / "output" / "lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
PAGE = "https://data.wublock123.com/dashboard/btc-indicators"


def _ensure_lark_copy() -> None:
    folder = Path(__file__).resolve().parent
    while folder != folder.parent and not (folder / "lark_copy.py").is_file():
        folder = folder.parent
    if str(folder) not in __import__("sys").path:
        __import__("sys").path.insert(0, str(folder))


_ensure_lark_copy()
from lark_copy import as_news  # noqa: E402


def env() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            out[key] = os.environ[key]
    return out


def load_doc() -> dict[str, Any]:
    for path in JSON_CANDIDATES:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError("longs.json missing")


def pct(now: float, base: float | None) -> str:
    if not base:
        return ""
    change = (now - base) / base * 100
    return f"{change:+.1f}%"


def fresh(stamp: str) -> bool:
    try:
        moment = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - moment <= timedelta(days=2)


def news(doc: dict[str, Any]) -> str:
    latest = doc["latest"]
    btc = float(latest["btc"])
    bits = [f"截至 {latest.get('time')}，Bitfinex 比特币保证金多头约 {btc:,.0f} 枚，为 2023 年 12 月以来最高"]
    week = pct(btc, doc.get("week_ago_btc"))
    ytd = pct(btc, doc.get("ytd_btc"))
    if week:
        bits.append(f"近 7 日 {week}")
    if ytd:
        bits.append(f"今年以来 {ytd}")
    body = (
        "据吴说数据中心统计，"
        + "，".join(bits)
        + "。数据来自 Bitfinex 公开持仓统计，不是 True Market Mean，也不是 200 日均线。"
        f"详见：{PAGE}"
    )
    return as_news(body)


def send(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(
            hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
        ).decode()
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        result = json.loads(resp.read().decode() or "{}")
    if str(result.get("code", "0")) != "0":
        raise RuntimeError(result)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    doc = load_doc()
    latest = doc.get("latest") or {}
    btc = latest.get("btc")
    stamp = str(latest.get("time") or "")
    if not doc.get("is_high_since_2023_12") or not isinstance(btc, (int, float)) or not fresh(stamp):
        print(f"skip high={doc.get('is_high_since_2023_12')} time={stamp}")
        return
    sent = 0.0
    if STATE_PATH.is_file():
        loaded = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and isinstance(loaded.get("btc"), (int, float)):
            sent = float(loaded["btc"])
    if float(btc) <= sent:
        print(f"already sent {sent:.2f}")
        return
    text = news(doc)
    print(text)
    if not args.push:
        return
    creds = env()
    webhook = creds.get("LARK_ALERT_WEBHOOK", "").strip()
    if not webhook:
        raise SystemExit("LARK_ALERT_WEBHOOK missing")
    send(webhook, creds.get("LARK_ALERT_SECRET"), text)
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps({"btc": btc, "time": stamp, "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
        + "\n",
        encoding="utf-8",
    )
    print("pushed", btc)


if __name__ == "__main__":
    main()
