#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hyperliquid 巨鲸净多头创今年新高 → Lark 新闻体。

只看最新完整 UTC 日：净多头>0 且严格高于同年此前所有日，才发；不回扫。
口径 CoinGlass，不是 Glassnode。
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
JSON_CANDIDATES = [
    HERE / "output" / "json" / "whale_net_long.json",
    Path("/root/dashboard/public/json/hyperliquid/whale_net_long.json"),
]
STATE_PATH = HERE / "output" / "whale_net_long_lark.json"
PAGE = "https://data.wublock123.com/dashboard/hyperliquid"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
FRESH_DAYS = 3

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


def load_doc() -> dict[str, Any]:
    for path in JSON_CANDIDATES:
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("latest"), dict):
                print(f"[whale-net-long] json={path}")
                return doc
    raise FileNotFoundError("找不到 whale_net_long.json")


def yi(usd: float) -> str:
    return f"{usd / 1e8:.2f} 亿美元"


def build_news(latest: dict[str, Any], prior: dict[str, Any] | None) -> str:
    day = str(latest["day"])
    y, m, d = day.split("-")
    net = float(latest["net_usd"])
    long_usd = float(latest["long_usd"])
    short_usd = float(latest["short_usd"])
    prior_bit = ""
    if prior and isinstance(prior.get("net_usd"), (int, float)):
        prior_bit = (
            f"此前同年高点为 {prior['day']} 的净多头约 {yi(float(prior['net_usd']))}。"
        )
    return (
        f"据吴说数据中心统计，截至 {int(y)} 年 {int(m)} 月 {int(d)} 日，"
        f"按 CoinGlass 口径，Hyperliquid 巨鲸持仓合计净多头约 {yi(net)}，"
        f"创 {int(y)} 年以来新高。多头名义约 {yi(long_usd)}，空头名义约 {yi(short_usd)}。"
        f"{prior_bit}"
        f"口径为名义约超过 100 万美元的持仓加总，不是 Glassnode 分群序列。"
        f"\n{PAGE}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        doc = load_doc()
    except (OSError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    latest = doc["latest"]
    day = str(latest.get("day") or "")
    net = latest.get("net_usd")
    if not day or not isinstance(net, (int, float)):
        print("[ERROR] latest 缺 day/net_usd", file=sys.stderr)
        return 1
    age = (datetime.now(timezone.utc).date() - datetime.strptime(day, "%Y-%m-%d").date()).days
    if age > FRESH_DAYS:
        print(f"[skip] {day} 过旧 age={age}")
        return 0
    if float(net) <= 0:
        print(f"[skip] 净多头非正 net={net}")
        return 0
    year = day[:4]
    rows = [
        row
        for row in (doc.get("data") or [])
        if isinstance(row, dict)
        and str(row.get("day") or "").startswith(year)
        and isinstance(row.get("net_usd"), (int, float))
    ]
    rows.sort(key=lambda row: str(row["day"]))
    priors = [row for row in rows if str(row["day"]) < day]
    if not args.force and any(float(row["net_usd"]) >= float(net) for row in priors):
        print(f"[skip] 不是 {year} 年新高 net={net}")
        return 0
    if len(priors) < 1 and not args.force:
        # 只有一天时不能叫“创今年新高”
        print("[skip] 历史不足，首日只建档")
        return 0
    prior_best = max(priors, key=lambda row: float(row["net_usd"])) if priors else None
    text = build_news(latest, prior_best)
    msg_id = f"hl-whale-netlong-{day}"
    print("---")
    print(f"id={msg_id}")
    print(as_news(text))
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0
    state = load_state()
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
        return 0
    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    try:
        send_lark(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Lark 失败: {exc}", file=sys.stderr)
        return 1
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-60:]
    save_state(state)
    print(f"[pushed] {msg_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
