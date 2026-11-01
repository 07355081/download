# -*- coding: utf-8 -*-
"""Spark 储蓄存款 → Lark 新闻。只发最新一天，不补历史。

数据日比上海今天早超过 2 天则不发，避免日常稿用旧数。
同一天只发一次。

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
JSON_PATH = HERE / "output" / "json" / "spark_savings.json"
STATE_PATH = HERE / "output" / "spark_savings_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/spark-savings"
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


def yi(value: float) -> str:
    return f"{value / 1e8:.2f} 亿美元"


def zh_day(iso: str) -> str:
    year, month, day = iso.split("-")
    return f"{int(year)} 年 {int(month)} 月 {int(day)} 日"


def build_news(latest: dict[str, Any]) -> str:
    total = float(latest["total_usd"])
    susds = float(latest["susds_usd"])
    share = float(latest["susds_share"])
    text = (
        f"据吴说数据中心统计，截至 {zh_day(str(latest['date']))}，"
        f"Spark 储蓄存款合计约 {yi(total)}，"
        f"其中 sUSDS 约 {yi(susds)}，占 {share * 100:.1f}%。"
    )
    if latest.get("is_high"):
        text += "这是该序列目前的最高水平。"
    text += f"\n{PAGE}"
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    if not JSON_PATH.is_file():
        print("[ERROR] 缺少 spark_savings.json", file=sys.stderr)
        return 1
    doc = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    latest = doc.get("latest") if isinstance(doc, dict) else None
    if not isinstance(latest, dict) or not isinstance(latest.get("date"), str):
        print("[ERROR] JSON 没有最新一天", file=sys.stderr)
        return 1
    day = datetime.strptime(latest["date"], "%Y-%m-%d").date()
    lag = (datetime.now(TZ_CN).date() - day).days
    if lag > MAX_LAG_DAYS:
        print(f"[skip] 数据停在 {latest['date']}，比今天早 {lag} 天，不发日常")
        return 0
    text = build_news(latest)
    msg_id = f"spark-savings-{latest['date']}"
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
