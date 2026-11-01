#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BTC 哈希价格 → Lark 新闻体。

过线（只发最新完整日，不回扫）：
- 较一周前 |Δ| ≥ 5%
- 或较近 30 日高点回撤 ≥ 8%
首日只建基线不推。超过 2 天不发。

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
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    HERE / "output" / "json" / "btc_hashprice.json",
    Path("/root/dashboard/public/json/btc-hashprice/btc_hashprice.json"),
]
STATE_PATH = HERE / "output" / "btc_hashprice_lark.json"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/btc-hashprice"
FEE_PAGE = "https://data.wublock123.com/dashboard/mining_shutdown"
WOW_MIN = 5.0
DRAWDOWN_MIN = 8.0
MAX_AGE_DAYS = 2
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
    empty = {"sentIds": [], "baseline": False, "wow_key": None, "dd_key": None}
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
        "sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else [],
        "baseline": bool(doc.get("baseline")),
        "wow_key": doc.get("wow_key"),
        "dd_key": doc.get("dd_key"),
    }


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
                print(f"[btc-hashprice] json={path}")
                return doc
    raise FileNotFoundError("找不到 btc_hashprice.json")


def zh_day(day: str) -> str:
    year, month, dom = day.split("-")
    return f"{int(year)} 年 {int(month)} 月 {int(dom)} 日"


def usd(v: float) -> str:
    return f"{v:.2f} 美元 / PH/s / 日"


def evaluate(latest: dict[str, Any], state: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    reasons: list[str] = []
    day = str(latest.get("day") or "")
    wow = latest.get("wow_pct")
    dd = latest.get("vs_30d_high_pct")
    high = latest.get("high_30d")
    high_day = latest.get("high_30d_day")
    wow_key = (
        f"{day}:{float(wow):+.1f}"
        if isinstance(wow, (int, float)) and abs(float(wow)) >= WOW_MIN and day
        else None
    )
    dd_key = (
        f"{day}:{float(dd):.1f}:{high_day}"
        if (
            isinstance(dd, (int, float))
            and float(dd) <= -DRAWDOWN_MIN
            and isinstance(high_day, str)
            and day
        )
        else None
    )

    if not state.get("baseline"):
        state["baseline"] = True
        if wow_key:
            state["wow_key"] = wow_key
        if dd_key:
            state["dd_key"] = dd_key
        return [], state

    if wow_key and state.get("wow_key") != wow_key:
        sign = "上涨" if float(wow) >= 0 else "下跌"
        from_day = latest.get("wow_from")
        from_usd = latest.get("wow_from_usd")
        bit = f"较一周前{sign}约 {abs(float(wow)):.2f}%"
        if isinstance(from_day, str) and isinstance(from_usd, (int, float)):
            bit += f"（{zh_day(from_day)}约 {usd(float(from_usd))}）"
        reasons.append(bit)
        state["wow_key"] = wow_key

    if dd_key and state.get("dd_key") != dd_key and isinstance(high, (int, float)):
        reasons.append(
            f"较近 30 日高点（{zh_day(str(high_day))}约 {usd(float(high))}）回撤约 {abs(float(dd)):.2f}%"
        )
        state["dd_key"] = dd_key
    return reasons, state


def build_news(latest: dict[str, Any], reasons: list[str]) -> str:
    day = str(latest["day"])
    hp = float(latest["hashprice_usd"])
    lead = "；".join(reasons) if reasons else "哈希价格过线"
    bits = [f"{zh_day(day)}比特币矿工哈希价格约 {usd(hp)}"]
    bits.append(lead)
    fee = latest.get("fee_share_pct")
    fee_day = latest.get("fee_day") or day
    if isinstance(fee, (int, float)):
        bits.append(f"同期链上手续费占区块奖励约 {float(fee):.2f}%（{zh_day(str(fee_day)[:10])}）")
    bits.append("矿工收入仍主要取决于 BTC 价格、挖矿难度与哈希价格变化")
    return (
        f"据吴说数据中心统计，{'；'.join(bits)}。"
        f"哈希价格口径为公开日序列（USD/PH/s/日），与 Hashrate Index 同主题但非同一指数；"
        f"手续费占比为本站 mempool 口径。"
        f"\n{PAGE}\n手续费占比图：{FEE_PAGE}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    doc = load_doc()
    latest = doc["latest"]
    day = str(latest.get("day") or "")
    age = (datetime.now(timezone.utc).date() - datetime.strptime(day, "%Y-%m-%d").date()).days
    print(
        f"[btc-hashprice] day={day} hp={latest.get('hashprice_usd')} "
        f"wow={latest.get('wow_pct')} dd={latest.get('vs_30d_high_pct')} age={age}"
    )
    if age > MAX_AGE_DAYS:
        print(f"[btc-hashprice] skip stale age={age}")
        return 0
    state = load_state()
    reasons, state = evaluate(latest, state)
    if not reasons:
        save_state(state)
        print("[btc-hashprice] no trigger / baseline")
        return 0
    msg_id = f"btc-hashprice-{day}-{hashlib.sha1('|'.join(reasons).encode()).hexdigest()[:8]}"
    if msg_id in state["sentIds"]:
        print(f"[btc-hashprice] already {msg_id}")
        return 0
    text = build_news(latest, reasons)
    if not args.push:
        print(text)
        return 0
    env = load_env()
    webhook = env.get("LARK_ALERT_WEBHOOK") or ""
    if not webhook:
        raise RuntimeError("缺少 LARK_ALERT_WEBHOOK")
    send_lark(webhook, env.get("LARK_ALERT_SECRET"), text)
    state["sentIds"] = [*state["sentIds"], msg_id][-40:]
    save_state(state)
    print(f"[btc-hashprice] pushed {msg_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
