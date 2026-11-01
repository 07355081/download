#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""比特币手续费占区块奖励比重创序列新低 → Lark 新闻体。

数据：mining_shutdown_price.json 的 feeInRewardPct（mempool，手续费 / 区块奖励）。
只发最新一个完整日，且该日低于此前每一天。超过 3 天不发。不回扫旧低点。

用法：
  python fee_share_flash.py
  python fee_share_flash.py --push
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
    HERE / "output" / "json" / "mining_shutdown_price.json",
    Path("/root/dashboard/public/json/mining-shutdown-price/mining_shutdown_price.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "mining-shutdown-price" / "mining_shutdown_price.json",
]
STATE_PATH = HERE / "output" / "fee_share_lark.json"
PAGE = "https://data.wublock123.com/dashboard/mining_shutdown"
MAX_AGE_DAYS = 3
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
        body = resp.read().decode("utf-8", errors="replace")
    if body:
        result = json.loads(body)
        if str(result.get("code", "0")) != "0":
            raise RuntimeError(f"Lark 返回异常: {result}")


def load_series() -> list[tuple[str, float]]:
    path = next((item for item in JSON_CANDIDATES if item.is_file()), None)
    if path is None:
        raise FileNotFoundError("找不到 mining_shutdown_price.json")
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = doc.get("data") if isinstance(doc, dict) else doc
    series: list[tuple[str, float]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        day = str(row.get("date") or "")[:10]
        fee = row.get("feeInRewardPct")
        if len(day) != 10 or not isinstance(fee, (int, float)):
            continue
        series.append((day, float(fee)))
    series.sort()
    if len(series) < 30:
        raise RuntimeError(f"手续费占比序列太短: {len(series)}")
    print(f"[fee-share] series ← {path} n={len(series)} last={series[-1][0]}")
    return series


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


def zh_day(day: str) -> str:
    year, month, dom = day.split("-")
    return f"{int(year)} 年 {int(month)} 月 {int(dom)} 日"


def pct(value: float) -> str:
    return f"{value:.2f}%"


def median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def build_text(day: str, value: float, prev_day: str, prev: float, mid: float) -> str:
    return (
        f"据吴说数据中心统计，{zh_day(day)}比特币链上手续费占区块奖励比重为 {pct(value)}，"
        f"创 2016 年 7 月以来新低。此前低点为 {zh_day(prev_day)}的 {pct(prev)}。"
        f"全史中位数约 {pct(mid)}。\n{PAGE}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    series = load_series()
    day, value = series[-1]
    prior = series[:-1]
    prev_day, prev = min(prior, key=lambda item: item[1])
    age = (datetime.now(timezone.utc).date() - datetime.strptime(day, "%Y-%m-%d").date()).days
    msg_id = f"fee-share-low-{day}"
    print(f"[fee-share] day={day} pct={value:.4f} prev={prev_day} {prev:.4f} age={age}")
    if age > MAX_AGE_DAYS:
        print(f"[fee-share] skip stale age={age}")
        return 0
    if value >= prev:
        print("[fee-share] skip not a new low")
        return 0
    text = build_text(day, value, prev_day, prev, median([item[1] for item in series]))
    state = load_state()
    if msg_id in state["sentIds"]:
        print(f"[fee-share] already {msg_id}")
        return 0
    if not args.push:
        print(text)
        return 0
    env = load_env()
    webhook = env.get("LARK_ALERT_WEBHOOK") or ""
    if not webhook:
        raise RuntimeError("缺少 LARK_ALERT_WEBHOOK")
    send_lark(webhook, env.get("LARK_ALERT_SECRET"), text)
    state["sentIds"] = [*state["sentIds"], msg_id][-30:]
    save_state(state)
    print(f"[fee-share] Lark OK {msg_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[fee-share] ERROR {exc}", file=sys.stderr)
        raise SystemExit(1)
