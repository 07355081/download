#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hyperliquid 与公链近 30 日净收入 → Lark 新闻。

只发最新一条，同一数据日一次。不加警报头，不回扫。
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
    HERE / "output" / "json" / "hl_net_revenue.json",
    Path("/root/dashboard/public/json/hl-net-revenue/hl_net_revenue.json"),
]
STATE_PATH = HERE / "output" / "hl_net_revenue_lark.json"
LOG_PATH = HERE / "hl_net_revenue_flash.log"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/hl-net-revenue"
sys.path.insert(0, str(ROOT))
from lark_copy import as_news  # noqa: E402


def load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def merge_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        env.update(load_env_file(path))
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
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
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def append_log(stamp: str, msg_id: str, ok: bool, text: str) -> None:
    line = json.dumps({"ts": stamp, "id": msg_id, "ok": ok, "text": text}, ensure_ascii=False)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def load_doc() -> dict[str, Any]:
    for path in JSON_CANDIDATES:
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("latest"), dict):
                print(f"[hl-net-revenue] json={path}")
                return doc
    raise FileNotFoundError("找不到 hl_net_revenue.json")


def wan(usd: float) -> str:
    return f"{usd / 1e4:.0f}万美元"


def build_news(latest: dict[str, Any]) -> str:
    day = str(latest["day"])
    year, month, date = day.split("-")
    rows = {
        str(row.get("slug")): row
        for row in (latest.get("rows") or [])
        if isinstance(row, dict)
    }
    hl = float(rows["hyperliquid-perps"]["usd"])
    eth = float(rows["ethereum"]["usd"])
    sol = float(rows["solana"]["usd"])
    bnb = float(rows["bsc"]["usd"])
    ratio = f"{hl / eth:.1f}" if eth > 0 else ""
    versus = f"，约为 Ethereum 同期{wan(eth)}的 {ratio} 倍" if ratio else ""
    text = (
        f"吴说获悉，据吴说数据中心统计，截至 {year} 年 {int(month)} 月 {int(date)} 日，"
        f"Hyperliquid 永续近 30 日净收入约{wan(hl)}{versus}。"
        f"同期 Solana 约{wan(sol)}，BNB Chain 约{wan(bnb)}。"
        f"口径为 DefiLlama 净收入，窗口为最近 30 个完整日。"
        f"\n{PAGE}"
    )
    return as_news(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        doc = load_doc()
    except (OSError, json.JSONDecodeError, FileNotFoundError, KeyError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1
    latest = doc["latest"]
    day = str(latest.get("day") or "")
    try:
        age = (datetime.now(timezone.utc).date() - datetime.strptime(day, "%Y-%m-%d").date()).days
    except ValueError:
        print(f"[ERROR] 日期无效: {day}", file=sys.stderr)
        return 1
    if age > 2:
        print(f"[skip] latest {day} too stale")
        return 0
    msg_id = f"hl-net-revenue-{day}"
    try:
        text = build_news(latest)
    except (KeyError, TypeError, ValueError) as exc:
        print(f"[ERROR] 组稿失败: {exc}", file=sys.stderr)
        return 1
    state = load_state()
    print("---")
    print(f"id={msg_id} already={msg_id in state['sentIds']}")
    print(text)
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0
    env = merge_env()
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
    state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-200:]
    save_state(state)
    append_log(stamp, msg_id, True, text)
    print(f"[ok] {msg_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
