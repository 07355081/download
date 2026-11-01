#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代币化黄金季度现货成交 → Lark 新闻。

只发刚收完的完整季度。季度成交高于上一完整自然年，或创已存季度新高，才发。
更早的季度不补发。

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
    HERE / "output" / "json" / "tokenized_gold_vol.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "tokenized-gold-vol" / "tokenized_gold_vol.json",
    Path("/root/dashboard/public/json/tokenized-gold-vol/tokenized_gold_vol.json"),
]
STATE_PATH = HERE / "output" / "lark_state.json"
LOG_PATH = HERE / "flash.log"
PAGE = "https://data.wublock123.com/json/tokenized-gold-vol/tokenized_gold_vol.json"
FRESH_DAYS = 14
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


def load_doc() -> dict[str, Any]:
    for path in JSON_CANDIDATES:
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                print(f"[tokenized-gold-vol] json={path}")
                return doc
    raise FileNotFoundError("找不到 tokenized_gold_vol.json")


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": []}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": []}
    ids = doc.get("sentIds") if isinstance(doc, dict) else None
    return {"sentIds": [item for item in ids if isinstance(item, str)] if isinstance(ids, list) else []}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


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


def yi(usd: float) -> str:
    if usd >= 1e8:
        return f"{usd / 1e8:.2f} 亿美元"
    if usd >= 1e4:
        return f"{usd / 1e4:.0f} 万美元"
    return f"{usd:.0f} 美元"


def build_news(latest: dict[str, Any], prior: list[dict[str, Any]], years: list[dict[str, Any]]) -> str | None:
    total = float(latest.get("total") or 0)
    if total <= 0:
        return None
    prior_max = max((float(row.get("total") or 0) for row in prior), default=0.0)
    ath = total > prior_max + 1e-6 if prior else False
    prev_year = next(
        (row for row in reversed(years) if int(row.get("year") or 0) == int(latest["year"]) - 1 and row.get("complete")),
        None,
    )
    beat_year = bool(prev_year and total > float(prev_year.get("total") or 0))
    if not ath and not beat_year:
        return None
    paxg = float(latest.get("paxg") or 0)
    xaut = float(latest.get("xaut") or 0)
    bits = [
        f"据吴说数据中心统计，{latest['year']} 年第 {latest['quarter']} 季度代币化黄金现货成交约 {yi(total)}"
        f"（PAXG 约 {yi(paxg)}，XAUT 约 {yi(xaut)}）。"
    ]
    if beat_year and prev_year:
        bits.append(
            f"已超过 {int(prev_year['year'])} 年全年约 {yi(float(prev_year['total']))}。"
        )
    if ath and prior:
        bits.append(f"高于此前每一个完整季度（此前最高约 {yi(prior_max)}）。")
    bits.append("口径是 CoinGecko 上 PAXG 与 XAUT 日成交额加总，不是整板块研报。")
    return "".join(bits) + f"\n{PAGE}"


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
    quarters = [row for row in (doc.get("quarters") or []) if isinstance(row, dict)]
    years = [row for row in (doc.get("years") or []) if isinstance(row, dict)]
    if not quarters:
        print("[skip] 还没有完整季度")
        return 0
    latest = quarters[-1]
    end = datetime.strptime(str(latest["end"]), "%Y-%m-%d").date()
    today = datetime.now(timezone.utc).date()
    age = (today - end).days
    print(
        f"[tokenized-gold-vol] latest={latest['year']}Q{latest['quarter']} "
        f"total={float(latest['total']):.0f} age={age}d"
    )
    if age > FRESH_DAYS and not args.force:
        print(f"[skip] 季结束已 {age} 天，超过 {FRESH_DAYS} 天，不补历史")
        return 0
    text = build_news(latest, quarters[:-1], years)
    if not text:
        print("[skip] 最新完整季未超上年全年，也未创已存季度新高")
        return 0
    msg_id = f"tokenized-gold-vol-{latest['year']}Q{latest['quarter']}"
    state = load_state()
    print("---")
    print(f"id={msg_id} already={msg_id in state['sentIds']}")
    print(as_news(text))
    if msg_id in state["sentIds"] and not args.force:
        print("[skip] already")
        return 0
    if not args.push:
        print("[dry-run] 未推送")
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
