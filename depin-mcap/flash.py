#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DePIN 赛道市值 → Lark 新闻。

CoinGecko categories id=depin。只发最新一条新闻稿，不加警报头，不回扫旧日期。
同一 UTC 日只发一次。
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
    HERE / "output" / "json" / "depin_mcap.json",
    Path("/root/dashboard/public/json/depin-mcap/depin_mcap.json"),
]
STATE_PATH = HERE / "output" / "depin_mcap_lark.json"
LOG_PATH = HERE / "depin_mcap_flash.log"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
JSON_URL = "https://data.wublock123.com/dashboard/lark-feeds/depin-mcap"

PAGE = "https://data.wublock123.com/dashboard/lark-feeds/depin-mcap"
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
                print(f"[depin-mcap] json={path}")
                return doc
    raise FileNotFoundError("找不到 depin_mcap.json")


def yi(usd: float) -> str:
    return f"{usd / 1e8:.2f} 亿美元"


def pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def build_news(doc: dict[str, Any]) -> str:
    latest = doc["latest"]
    day = str(latest["time"])
    mcap = float(latest["market_cap"])
    ch24 = latest.get("market_cap_change_24h")
    ch_bit = f"，过去 24 小时约 {pct(float(ch24))}" if isinstance(ch24, (int, float)) else ""
    tops = doc.get("latest_top") if isinstance(doc.get("latest_top"), list) else []
    top_bits = []
    for item in tops[:5]:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").upper()
        value = item.get("market_cap")
        if symbol and isinstance(value, (int, float)):
            top_bits.append(f"{symbol} 约 {yi(float(value))}")
    top_line = f"市值靠前的包括 {('、'.join(top_bits))}。" if top_bits else ""
    # change vs earliest stored day if we have enough history
    hist = [row for row in (doc.get("data") or []) if isinstance(row, dict) and isinstance(row.get("market_cap"), (int, float))]
    hist_bit = ""
    if len(hist) >= 2:
        first = hist[0]
        base = float(first["market_cap"])
        if base > 0:
            hist_bit = (
                f"本通道自 {first['time']} 起累计约 {pct((mcap / base - 1) * 100)}"
                f"（起点约 {yi(base)}）。"
            )
    year, month, date = day.split("-")
    text = (
        f"吴说获悉，据吴说数据中心统计，截至 {year} 年 {int(month)} 月 {int(date)} 日，"
        f"按 CoinGecko DePIN 分类，去中心化物理基础设施网络赛道代币总市值约 {yi(mcap)}{ch_bit}。"
        f"{top_line}{hist_bit}"
        f"口径为 CoinGecko，与 CryptoRank 等外电板块数字可能不一致。"
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
    except (OSError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    day = str(doc["latest"]["time"])
    msg_id = f"depin-mcap-{day}"
    text = build_news(doc)
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
    print(f"[pushed] {msg_id}")
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-200:]
    save_state(state)
    append_log(stamp, msg_id, True, text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
