#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""加密融资月度总额/笔数（RootData monthly_stats）→ Lark 新闻体。

网页 /dashboard/financing-news 已有月度序列。非 CryptoRank。
只发最新一个已收完自然月；月末后超过 45 天不发（防滞后）；不回扫旧月。
赛道拆分与「最活跃投资方」明细无稳定字段，正文不写。
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
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    Path("/root/dashboard/public/json/financing-news/monthly_stats.json"),
    ROOT / "financing-news" / "output" / "json" / "monthly_stats.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "financing-news" / "monthly_stats.json",
]
STATE_PATH = HERE / "output" / "financing_monthly_lark.json"
LOG_PATH = HERE / "flash.log"
PAGE = "https://data.wublock123.com/dashboard/financing-news"
FRESH_DAYS = 45
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
    raise FileNotFoundError("找不到 monthly_stats.json")


def load_months(path: Path) -> list[dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = doc.get("data") if isinstance(doc, dict) else doc
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("monthly_stats 无数据")
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        time_key = str(row.get("time") or "")
        if len(time_key) < 7:
            continue
        try:
            amount = float(row.get("amount_usd") or 0)
            count = int(row.get("deal_count") or 0)
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "time": time_key[:7],
                "deal_count": count,
                "amount_usd": amount,
                "amount_mom": row.get("amount_mom"),
                "count_mom": row.get("count_mom"),
            }
        )
    out.sort(key=lambda item: item["time"])
    return out


def month_end(ym: str) -> date:
    year, month = int(ym[:4]), int(ym[5:7])
    return date(year, month, monthrange(year, month)[1])


def fmt_usd(amount: float) -> str:
    yi = amount / 1e8
    if yi >= 10:
        return f"{yi:.1f} 亿美元".replace(".0 ", " ")
    if yi >= 1:
        return f"{yi:.2f} 亿美元"
    return f"{amount / 1e6:.0f} 万美元"


def fmt_pct(ratio: Any) -> str | None:
    if ratio is None:
        return None
    try:
        return f"{float(ratio) * 100:+.1f}%"
    except (TypeError, ValueError):
        return None


def build_news(latest: dict[str, Any], months: list[dict[str, Any]]) -> str:
    ym = latest["time"]
    year, month = int(ym[:4]), int(ym[5:7])
    bits = [
        f"据吴说数据中心统计，{year} 年 {month} 月加密融资约 {latest['deal_count']} 笔、"
        f"总额约 {fmt_usd(latest['amount_usd'])}"
    ]
    amt_mom = fmt_pct(latest.get("amount_mom"))
    cnt_mom = fmt_pct(latest.get("count_mom"))
    extras = []
    if amt_mom:
        extras.append(f"较上月金额 {amt_mom}")
    if cnt_mom:
        extras.append(f"笔数 {cnt_mom}")
    if extras:
        bits.append("，".join(extras))

    ytd = sum(m["amount_usd"] for m in months if m["time"][:4] == ym[:4] and m["time"] <= ym)
    bits.append(f"。{year} 年迄今累计融资额约 {fmt_usd(ytd)}")

    # 若确为本序列自某高活跃期后的月度金额新低，点一句
    window = [m for m in months if m["time"] >= "2024-07" and m["time"] <= ym]
    if len(window) >= 6:
        low = min(window, key=lambda m: m["amount_usd"])
        if low["time"] == ym:
            bits.append("，为 2024 年 7 月以来月度融资额最低")

    return "".join(bits) + f"。口径为 RootData 融资明细加总，非 CryptoRank。\n{PAGE}"


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        months = load_months(resolve_json())
    except (OSError, RuntimeError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    latest = months[-1]
    end = month_end(latest["time"])
    age = (date.today() - end).days
    print(
        f"[financing-monthly] latest={latest['time']} deals={latest['deal_count']} "
        f"amt_m={latest['amount_usd']/1e6:.1f} age={age}d fresh<={FRESH_DAYS}"
    )
    if age > FRESH_DAYS and not args.force:
        print(f"[skip] 月末已过 {age} 天，不发（防滞后）")
        return 0

    msg_id = f"financing-monthly-{latest['time']}"
    text = as_news(build_news(latest, months))
    print("---")
    print(f"id={msg_id}")
    print(text)
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0

    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    state = load_state()
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
        return 0
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
    state["sentIds"] = state["sentIds"][-100:]
    save_state(state)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} {msg_id} ok=True\n{text}\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
