#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ETH 质押总量 / 占供应 / 入口队列 → Lark 新闻体。

只发最新完整日（距今 ≤2 天）。不回扫历史日。
正文可直接发稿；若质押量相对此前全日创高，句中写「创历史新高」。
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
    HERE / "output" / "json" / "eth_staking.json",
    Path("/root/dashboard/public/json/eth-staking/eth_staking.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "eth-staking" / "eth_staking.json",
]
STATE_PATH = HERE / "output" / "eth_staking_lark.json"
LOG_PATH = HERE / "flash.log"
PAGE = "https://data.wublock123.com/dashboard/eth-indicators"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
FRESH_DAYS = 2

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
    body = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": body}}
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
        raw = resp.read().decode("utf-8", errors="replace")
    if raw:
        result = json.loads(raw)
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


def load_rows() -> list[dict[str, Any]]:
    for path in JSON_CANDIDATES:
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        rows = doc.get("data") if isinstance(doc, dict) else None
        if isinstance(rows, list):
            out = [r for r in rows if isinstance(r, dict) and r.get("day")]
            out.sort(key=lambda r: str(r["day"]))
            print(f"[eth-staking] json={path} n={len(out)}")
            return out
    raise FileNotFoundError("找不到 eth_staking.json，请先跑 download.py")


def cn_day(day: str) -> str:
    y, m, d = day.split("-")
    return f"{int(y)} 年 {int(m)} 月 {int(d)} 日"


def eth_text(n: float) -> str:
    if n >= 100_000:
        return f"{n / 10_000:.0f} 万枚"
    return f"{n:,.0f} 枚"


def fresh(day: str) -> bool:
    d = datetime.strptime(day, "%Y-%m-%d").date()
    return (datetime.now(timezone.utc).date() - d).days <= FRESH_DAYS


def is_ath(rows: list[dict[str, Any]], day: str) -> bool:
    cur = next((r for r in rows if str(r["day"]) == day), None)
    if not cur or not isinstance(cur.get("staked_eth"), (int, float)):
        return False
    prior = [
        float(r["staked_eth"])
        for r in rows
        if str(r["day"]) < day and isinstance(r.get("staked_eth"), (int, float))
    ]
    if len(prior) < 30:
        return False
    return float(cur["staked_eth"]) > max(prior)


def news_text(row: dict[str, Any], ath: bool) -> str:
    staked = float(row["staked_eth"])
    pct = row.get("staked_pct")
    entry = row.get("entry_queue_eth")
    bits = [
        f"据吴说数据中心统计，截至 {cn_day(str(row['day']))}，",
        f"以太坊质押总量约 {eth_text(staked)}",
    ]
    if ath:
        bits.append("，创历史新高")
    if isinstance(pct, (int, float)):
        bits.append(f"，约占供应 {float(pct):.2f}%")
    bits.append("。")
    if isinstance(entry, (int, float)):
        bits.append(f"质押入口队列仍约有 {eth_text(float(entry))}等待进入。")
    bits.append("口径为 validatorqueue.com 公开日序列。")
    bits.append(f"\n{PAGE}")
    return "".join(bits)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        rows = load_rows()
    except (OSError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    if not rows:
        print("[eth-staking] 无数据")
        return 0
    latest = rows[-1]
    day = str(latest["day"])
    if not fresh(day):
        print(f"[skip] 最新日 {day} 超过 {FRESH_DAYS} 天，不发")
        return 0
    ath = is_ath(rows, day)
    msg_id = f"eth-staking-daily-{day}"
    text = news_text(latest, ath)
    state = load_state()
    print("---")
    print(f"id={msg_id} ath={ath} already={msg_id in state['sentIds']}")
    print(as_news(text))
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
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
        print(f"[ERROR] Lark 失败 {msg_id}: {exc}", file=sys.stderr)
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
