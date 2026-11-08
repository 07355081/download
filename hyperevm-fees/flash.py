# -*- coding: utf-8 -*-
"""HyperEVM 链上日费用创近一季度高点 → Lark 新闻。

只发最新一个完整 UTC 日，且费用为近 90 个完整日最高；
日龄超过 3 天不发；不回扫历史。

用法：
  python flash.py
  python flash.py --push
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
    HERE / "output" / "json" / "hyperevm_fees.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "hyperevm-fees" / "hyperevm_fees.json",
    Path("/root/dashboard/public/json/hyperevm-fees/hyperevm_fees.json"),
]
STATE_PATH = HERE / "output" / "hyperevm_fees_lark.json"
LOG_PATH = HERE / "flash.log"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/hyperevm-fees"
TZ_CN = timezone(timedelta(hours=8))
FRESH_DAYS = 3
LOOKBACK = 90
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]


def _ensure_lark_copy() -> None:
    folder = Path(__file__).resolve().parent
    while folder != folder.parent and not (folder / "lark_copy.py").is_file():
        folder = folder.parent
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))


_ensure_lark_copy()
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
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
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
            raise RuntimeError(result)


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


def resolve_json() -> Path:
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 hyperevm_fees.json，请先跑 download.py")


def wan(usd: float) -> str:
    if abs(usd) >= 1e8:
        return f"{usd / 1e8:.2f} 亿美元"
    if abs(usd) >= 1e4:
        return f"{usd / 1e4:.1f} 万美元"
    return f"{usd:,.0f} 美元"


def cn_day(day: str) -> str:
    y, m, d = day.split("-")
    return f"{int(y)} 年 {int(m)} 月 {int(d)} 日"


def build_news(day: str, fees: float, prior_day: str | None, prior_high: float | None) -> str:
    prior = ""
    if prior_day and prior_high is not None:
        prior = f"高于近一季度此前最高的 {cn_day(prior_day)}（约 {wan(prior_high)}）。"
    return (
        f"吴说获悉，据吴说数据中心统计，{cn_day(day)} HyperEVM 网络链上费用约 {wan(fees)}，"
        f"为近一季度高点。{prior}"
        f"口径为 DefiLlama Hyperliquid L1 日费用，与看板 Hyperliquid 页 HyperEVM 链上费用对应；"
        f"不含全链交易笔数与 HYPE 销毁。"
        f"\n{PAGE}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    try:
        path = resolve_json()
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    rows = [
        r
        for r in (doc.get("data") or [])
        if isinstance(r, dict) and str(r.get("day") or "")[:10] and isinstance(r.get("fees_usd"), (int, float))
    ]
    rows.sort(key=lambda r: str(r["day"])[:10])
    if len(rows) < 2:
        print("[skip] 日序列不足", file=sys.stderr)
        return 0

    latest = rows[-1]
    day = str(latest["day"])[:10]
    fees = float(latest["fees_usd"])
    window = rows[-LOOKBACK:] if len(rows) >= LOOKBACK else rows
    prior = window[:-1]
    prior_high = max((float(r["fees_usd"]) for r in prior), default=None)
    prior_day = None
    if prior_high is not None:
        for r in prior:
            if abs(float(r["fees_usd"]) - prior_high) < 1e-6:
                prior_day = str(r["day"])[:10]
                break
    is_high = prior_high is None or fees > prior_high + 1.0
    age = (datetime.now(TZ_CN).date() - datetime.strptime(day, "%Y-%m-%d").date()).days
    fresh = 0 <= age <= FRESH_DAYS

    print(
        f"[hyperevm-fees] json={path} day={day} fees={fees:.0f} "
        f"q_high={is_high} age={age}d fresh={fresh}"
    )

    if not is_high and not args.force:
        print("[skip] 最新完整日未创近一季度费用高点")
        return 0
    if not fresh and not args.force:
        print(f"[skip] 日龄 {age} 天，超过 {FRESH_DAYS} 天（不补历史）")
        return 0
    if prior_high is None and not args.force:
        print("[skip] 窗口首日仅建基线，不推")
        return 0

    text = as_news(build_news(day, fees, prior_day, prior_high))
    msg_id = f"hyperevm-fees-qhigh-{day}"
    print("---")
    print(f"id={msg_id}")
    print(text)
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
        print(f"[ERROR] Lark: {exc}", file=sys.stderr)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"=== {stamp} {msg_id} ok=False {exc}\n{text}\n\n")
        return 1
    print(f"[pushed] {msg_id}")
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-120:]
    save_state(state)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} {msg_id} ok=True\n{text}\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
