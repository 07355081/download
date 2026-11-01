# -*- coding: utf-8 -*-
"""Paradex 锁仓、费用和 DIME → Lark 新闻。只发最新一天，不补历史。

锁仓日或费用日比上海今天早超过 2 天则不发。同一天只发一次。

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
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_PATH = HERE / "output" / "json" / "paradex.json"
STATE_PATH = HERE / "output" / "paradex_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/paradex"
MAX_LAG_DAYS = 2

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
        ts = str(int(datetime.now().timestamp()))
        payload["timestamp"] = ts
        payload["sign"] = gen_sign(secret, ts)
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


def usd(value: float) -> str:
    if value >= 1e8:
        return f"{value / 1e8:.2f} 亿美元"
    if value >= 1e6:
        return f"{value / 1e4:.0f} 万美元"
    if value >= 1e4:
        return f"{value / 1e4:.2f} 万美元"
    return f"{value:.0f} 美元"


def zh_day(iso: str) -> str:
    year, month, day = iso.split("-")
    return f"{int(year)} 年 {int(month)} 月 {int(day)} 日"


def build_news(latest: dict[str, Any]) -> str:
    token = latest["token"]
    text = (
        f"吴说获悉，据 DefiLlama 数据，截至 {zh_day(str(latest['date']))}，"
        f"衍生品协议 Paradex 锁仓规模约 {usd(float(latest['tvl_usd']))}"
    )
    peak_day = str(latest.get("peak_date") or "")
    if peak_day and peak_day != latest["date"] and float(latest.get("peak_tvl_usd") or 0) > float(latest["tvl_usd"]):
        text += (
            f"，较 {zh_day(peak_day)}峰值 {usd(float(latest['peak_tvl_usd']))}"
            f"回落约 {float(latest['drop_pct']):.0f}%"
        )
    text += f"。近 24 小时费用约 {usd(float(latest['fees_24h_usd']))}。"
    text += (
        f"另据 CoinGecko，代币 {token['symbol']} 当前市值约 {usd(float(token['mcap_usd']))}，"
        f"近 24 小时交易量约 {usd(float(token['volume_24h_usd']))}。"
    )
    text += f"\n{PAGE}"
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    if not JSON_PATH.is_file():
        print("[ERROR] 缺少 paradex.json", file=sys.stderr)
        return 1
    doc = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    latest = doc.get("latest") if isinstance(doc, dict) else None
    if not isinstance(latest, dict) or not isinstance(latest.get("date"), str):
        print("[ERROR] JSON 没有最新一天", file=sys.stderr)
        return 1
    today = datetime.now(TZ_CN).date()
    tvl_day = datetime.strptime(latest["date"], "%Y-%m-%d").date()
    fee_day = datetime.strptime(str(latest.get("fees_day")), "%Y-%m-%d").date()
    lag = max((today - tvl_day).days, (today - fee_day).days)
    if lag > MAX_LAG_DAYS:
        print(f"[skip] 数据停在锁仓 {latest['date']}、费用 {latest.get('fees_day')}，不发")
        return 0
    text = build_news(latest)
    msg_id = f"paradex-{latest['date']}"
    print(text)
    state = load_state()
    if msg_id in state["sentIds"]:
        print(f"[skip] 已发 {msg_id}")
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
    try:
        send_lark(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Lark 失败: {exc}", file=sys.stderr)
        return 1
    state["sentIds"] = (state["sentIds"] + [msg_id])[-60:]
    save_state(state)
    print(f"[pushed] {msg_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
