# -*- coding: utf-8 -*-
"""上半年融资轮次（RootData）→ Lark。只发最新已收完 H1。

6 月 30 日后超过 60 天不发。不回扫更早年份。不补 1/1–6/17 这种截断窗口。
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    HERE / "output" / "json" / "financing_h1.json",
    Path("/root/dashboard/public/json/financing-h1/financing_h1.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "financing-h1" / "financing_h1.json",
]
STATE_PATH = HERE / "output" / "financing_h1_lark.json"
PAGE = "https://data.wublock123.com/dashboard/financing-news"
FRESH_DAYS = 60
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
    raise FileNotFoundError("找不到 financing_h1.json")


def pct(curr: int, prev: int) -> str | None:
    if prev <= 0:
        return None
    return f"{(curr / prev - 1) * 100:+.1f}%"


def build_news(cur: dict[str, Any], prev: dict[str, Any] | None) -> str:
    year = int(cur["year"])
    bits = [
        f"据吴说数据中心统计，{year} 年上半年加密融资约 {int(cur['total'])} 笔",
        f"其中种子轮/Pre-Seed 约 {int(cur['seed'])} 笔",
        f"A 轮及以后约 {int(cur['a_plus'])} 笔",
    ]
    if prev:
        extras = []
        tot = pct(int(cur["total"]), int(prev["total"]))
        seed = pct(int(cur["seed"]), int(prev["seed"]))
        aplus = pct(int(cur["a_plus"]), int(prev["a_plus"]))
        if tot:
            extras.append(f"总笔数较 {prev['year']} 年上半年 {tot}")
        if seed:
            extras.append(f"种子轮/Pre-Seed {seed}")
        if aplus:
            extras.append(f"A 轮及以后 {aplus}")
        if extras:
            bits.append("；".join(extras))
    return (
        "吴说获悉，"
        + "，".join(bits)
        + "。口径为 RootData 融资明细 1 月 1 日至 6 月 30 日，非 CryptoRank。"
        f"\n{PAGE}"
    )


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
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    doc = json.loads(resolve_json().read_text(encoding="utf-8"))
    rows = [row for row in (doc.get("data") or []) if isinstance(row, dict) and row.get("complete")]
    if not rows:
        print("[skip] 无已收完上半年")
        return 0
    cur = rows[-1]
    year = int(cur["year"])
    age = (datetime.now(TZ_CN).date() - date(year, 6, 30)).days
    print(f"[financing-h1] year={year} total={cur['total']} seed={cur['seed']} a+={cur['a_plus']} age={age}")
    if age > FRESH_DAYS:
        print(f"[skip] H1 已结束 {age} 天，超过 {FRESH_DAYS} 天，不补历史")
        return 0
    prev = next((row for row in rows if int(row["year"]) == year - 1), None)
    msg_id = f"financing-h1-{year}"
    text = build_news(cur, prev)
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
    state["sentIds"] = (state["sentIds"] + [msg_id])[-20:]
    save_state(state)
    print(f"[pushed] {msg_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
