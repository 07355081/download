#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strive 公开 BTC 持仓对照 SpaceX / Coinbase → Lark 新闻。

只在最新快照相对上一已发快照持仓变化 ≥100 枚，或相对两家的高低关系变化时发送。
同一持仓档只发一次。不加警报头，不回扫历史，不写单笔均价。
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
    HERE / "output" / "json" / "strive_btc.json",
    Path("/root/dashboard/public/json/strive-btc/strive_btc.json"),
]
STATE_PATH = HERE / "output" / "strive_btc_lark.json"
LOG_PATH = HERE / "strive_btc_flash.log"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
JSON_URL = "https://data.wublock123.com/dashboard/treasuries"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/treasuries"
DELTA_MIN = 100.0

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
        return {"sentIds": [], "lastHoldings": None}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": [], "lastHoldings": None}
    if not isinstance(doc, dict):
        return {"sentIds": [], "lastHoldings": None}
    ids = doc.get("sentIds")
    return {
        "sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else [],
        "lastHoldings": doc.get("lastHoldings") if isinstance(doc.get("lastHoldings"), dict) else None,
    }


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
                print(f"[strive-btc] json={path}")
                return doc
    raise FileNotFoundError("找不到 strive_btc.json")


def btc_text(n: float) -> str:
    return f"{n:,.0f} 枚"


def yi(usd: float) -> str:
    return f"{usd / 1e8:.2f} 亿美元"


def above_line(strive: float, other: float, label: str) -> str:
    if strive > other:
        return f"高于 {label}"
    if strive < other:
        return f"低于 {label}"
    return f"与 {label} 持平"


def build_news(latest: dict[str, Any], prev: dict[str, Any] | None) -> str:
    entities = latest["entities"]
    day = str(latest["time"])
    strive = float(entities["strive"]["holdings"])
    spacex = float(entities["spacex"]["holdings"])
    coinbase = float(entities["coinbase"]["holdings"])
    value = entities["strive"].get("value_usd")
    value_bit = f"，对应市值约 {yi(float(value))}" if isinstance(value, (int, float)) else ""
    year, month, date = day.split("-")

    delta_bit = ""
    if prev and isinstance(prev.get("strive"), (int, float)):
        delta = strive - float(prev["strive"])
        if abs(delta) >= 1:
            verb = "增加" if delta > 0 else "减少"
            delta_bit = f"相对上一快照（{prev.get('time')}）约{verb} {abs(delta):,.0f} 枚。"

    text = (
        f"吴说获悉，据吴说数据中心统计，截至 {year} 年 {int(month)} 月 {int(date)} 日，"
        f"按 CoinGecko 上市公司比特币财库口径，Strive 公开持有约 {btc_text(strive)} BTC{value_bit}。"
        f"同期 SpaceX 约 {btc_text(spacex)}，Coinbase 约 {btc_text(coinbase)}，"
        f"Strive {above_line(strive, spacex, 'SpaceX')}，并{above_line(strive, coinbase, 'Coinbase')}。"
        f"{delta_bit}"
        f"口径为公开持仓总量，不是单笔买入价格。"
        f"\n{DASHBOARD_URL}"
    )
    return as_news(text)


def should_send(entities: dict[str, Any], prev_sent: dict[str, Any] | None) -> bool:
    if not prev_sent:
        return True
    strive = float(entities["strive"]["holdings"])
    spacex = float(entities["spacex"]["holdings"])
    coinbase = float(entities["coinbase"]["holdings"])
    old_s = float(prev_sent.get("strive") or 0)
    old_x = float(prev_sent.get("spacex") or 0)
    old_c = float(prev_sent.get("coinbase") or 0)
    if abs(strive - old_s) >= DELTA_MIN:
        return True
    rank_now = (strive > spacex, strive > coinbase)
    rank_old = (old_s > old_x, old_s > old_c)
    return rank_now != rank_old


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

    latest = doc["latest"]
    day = str(latest["time"])
    latest_dt = datetime.strptime(day, "%Y-%m-%d").date()
    if (datetime.now(timezone.utc).date() - latest_dt).days > 2:
        print(f"[skip] latest {day} too stale")
        return 0

    entities = latest["entities"]
    strive_i = int(round(float(entities["strive"]["holdings"])))
    msg_id = f"strive-btc-{strive_i}"
    state = load_state()
    prev_row = None
    data = [r for r in (doc.get("data") or []) if isinstance(r, dict) and str(r.get("time")) < day]
    if data:
        prev_row = data[-1]
    prev_sent = state.get("lastHoldings")
    text = build_news(latest, prev_row if isinstance(prev_row, dict) else None)
    send = args.force or should_send(entities, prev_sent if isinstance(prev_sent, dict) else None)
    print("---")
    print(f"id={msg_id} send={send} already={msg_id in state['sentIds']}")
    print(text)
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0
    if not send:
        print("[skip] holdings unchanged")
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
    state["lastHoldings"] = {
        "time": day,
        "strive": float(entities["strive"]["holdings"]),
        "spacex": float(entities["spacex"]["holdings"]),
        "coinbase": float(entities["coinbase"]["holdings"]),
    }
    save_state(state)
    append_log(stamp, msg_id, True, text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
