# -*- coding: utf-8 -*-
"""Fragment 协议费用 → Lark。只发最新完整日，不补历史。

口径：DefiLlama dailyFees，不是 Messari Telegram 产品结算。
数据日比上海今天早超过 2 天则不发。同一天只发一次。
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
JSON_CANDIDATES = [
    HERE / "output" / "json" / "fragment_fees.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "fragment" / "fragment_fees.json",
    Path("/root/dashboard/public/json/fragment/fragment_fees.json"),
]
STATE_PATH = HERE / "output" / "fragment_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/fragment"
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


def resolve_json() -> Path:
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 fragment_fees.json")


def wan(usd: float) -> str:
    return f"{usd / 1e4:.0f} 万美元"


def zh_day(iso: str) -> str:
    year, month, day = iso.split("-")
    return f"{int(year)} 年 {int(month)} 月 {int(day)} 日"


def build_news(doc: dict[str, Any]) -> tuple[str, str]:
    latest = doc.get("latest") if isinstance(doc, dict) else None
    if not isinstance(latest, dict):
        raise RuntimeError("JSON 没有 latest")
    day = str(latest.get("date") or "")
    fees = float(latest.get("fees_usd") or 0)
    last7 = float(doc.get("last7_usd") or 0)
    text = (
        f"据吴说数据中心统计，截至 {zh_day(day)}，"
        f"Fragment 协议费用约 {wan(fees)}。"
        f"近 7 个完整日合计约 {wan(last7)}。"
        f"口径为 DefiLlama 协议费用，不是 Telegram 产品结算额。"
        f"\n{PAGE}"
    )
    return f"fragment-fees-{day}", text


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": as_news(text)}}
    if secret:
        ts = str(int(datetime.now().timestamp()))
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    doc = json.loads(resolve_json().read_text(encoding="utf-8"))
    latest = doc.get("latest") if isinstance(doc, dict) else None
    if not isinstance(latest, dict) or not isinstance(latest.get("date"), str):
        print("[ERROR] JSON 没有最新一天", file=sys.stderr)
        return 1
    day = datetime.strptime(str(latest["date"]), "%Y-%m-%d").date()
    lag = (datetime.now(TZ_CN).date() - day).days
    if lag > MAX_LAG_DAYS:
        print(f"[skip] 数据停在 {latest['date']}，比今天早 {lag} 天")
        return 0
    msg_id, text = build_news(doc)
    print(msg_id)
    print(as_news(text))
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
    send_lark(webhook, secret, text)
    state["sentIds"] = (state["sentIds"] + [msg_id])[-60:]
    save_state(state)
    print(f"[pushed] {msg_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
