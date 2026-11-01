# -*- coding: utf-8 -*-
"""MSTR 跌破 100 美元 → Lark 新闻体。

从上方向下穿过 100 才发；已在下方不补发；回到上方后可再触发。
只发最新一次，不回扫。不是 STRC。
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
    HERE / "output" / "json" / "mstr_price.json",
    Path("/root/dashboard/public/json/mstr-price/mstr_price.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "mstr-price" / "mstr_price.json",
]
STATE_PATH = HERE / "output" / "mstr_price_lark.json"
PAGE = "https://data.wublock123.com/dashboard/treasuries"
LEVEL = 100.0
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


def resolve_json() -> Path:
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 mstr_price.json")


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


def load_state() -> dict[str, Any]:
    empty = {"sentIds": [], "armed": True, "last_price": None}
    if not STATE_PATH.is_file():
        return dict(empty)
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(empty)
    if not isinstance(doc, dict):
        return dict(empty)
    ids = doc.get("sentIds")
    return {
        "sentIds": [item for item in ids if isinstance(item, str)] if isinstance(ids, list) else [],
        "armed": bool(doc.get("armed", True)),
        "last_price": doc.get("last_price"),
    }


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def pct(value: float) -> str:
    return f"{value:+.2f}%"


def build_news(doc: dict[str, Any]) -> str:
    price = float(doc["price"])
    chg = float(doc.get("change_pct") or 0)
    low = doc.get("day_low")
    last_below = doc.get("last_close_below")
    session = doc.get("session_date") or ""
    bits = [
        f"Strategy（MSTR）现报约 {price:.2f} 美元",
        f"日内 {pct(chg)}",
        f"跌破 {LEVEL:.0f} 美元",
    ]
    if isinstance(low, (int, float)) and low < LEVEL <= price:
        bits.append(f"盘中低点约 {float(low):.2f} 美元")
    if last_below and last_below != session:
        y, m, d = last_below.split("-")
        bits.append(f"为自 {int(y)} 年 {int(m)} 月 {int(d)} 日收盘低于 {LEVEL:.0f} 美元以来再次跌破")
    return (
        "吴说获悉，据吴说数据中心统计，"
        + "，".join(bits)
        + "。口径为 Yahoo Finance 正股 MSTR，不是优先股 STRC。"
        f"\n{PAGE}"
    )


def evaluate(doc: dict[str, Any], state: dict[str, Any]) -> bool:
    price = float(doc["price"])
    low = doc.get("day_low")
    touched = price < LEVEL or (isinstance(low, (int, float)) and float(low) < LEVEL)
    cold = state.get("last_price") is None
    if cold and touched:
        state["armed"] = False
        state["last_price"] = price
        return False
    if not touched:
        state["armed"] = True
        state["last_price"] = price
        return False
    armed = bool(state.get("armed", True))
    state["last_price"] = price
    if armed:
        state["armed"] = False
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    doc = json.loads(resolve_json().read_text(encoding="utf-8"))
    state = load_state()
    fire = evaluate(doc, state)
    session = str(doc.get("session_date") or datetime.now(TZ_CN).date())
    msg_id = f"mstr-below-100-{session}"
    print(
        f"[mstr-price] px={doc['price']} low={doc.get('day_low')} "
        f"armed={state['armed']} fire={fire} {msg_id}"
    )
    if not fire:
        save_state(state)
        print("[skip] 未从上方向下穿过 100，不补历史")
        return 0
    text = as_news(build_news(doc))
    print(msg_id)
    print(text)
    if msg_id in state["sentIds"]:
        save_state(state)
        print(f"[skip] 已发 {msg_id}")
        return 0
    if not args.push:
        save_state(state)
        print("[dry-run] 未推送")
        return 0
    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    send_lark(webhook, secret, text)
    state["sentIds"] = (state["sentIds"] + [msg_id])[-40:]
    save_state(state)
    print(f"[pushed] {msg_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
