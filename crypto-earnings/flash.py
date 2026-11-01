#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""加密相关美股业绩 → Lark 新闻。

只发提交日在 10 天内、且未见过的最新一份业绩文件。
更早的季度只记入已见，不补发。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
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
    HERE / "output" / "json" / "crypto_earnings.json",
    Path("/root/dashboard/public/json/crypto-earnings/crypto_earnings.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "crypto-earnings" / "crypto_earnings.json",
]
STATE_PATH = HERE / "output" / "crypto_earnings_lark.json"
LOG_PATH = HERE / "flash.log"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/crypto-earnings"
FRESH_DAYS = 10
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
        body = resp.read().decode("utf-8", errors="replace")
    if body:
        result = json.loads(body)
        if str(result.get("code", "0")) != "0":
            raise RuntimeError(f"Lark 返回异常: {result}")


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"seen": []}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"seen": []}
    seen = doc.get("seen") if isinstance(doc, dict) else None
    return {"seen": [x for x in seen if isinstance(x, str)] if isinstance(seen, list) else []}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["seen"] = state.get("seen", [])[-400:]
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def load_doc() -> dict[str, Any]:
    for path in JSON_CANDIDATES:
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("filings"), list):
                return doc
    raise FileNotFoundError("找不到 crypto_earnings.json")


def age_days(filed: str) -> int | None:
    try:
        day = datetime.strptime(filed[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc).date() - day.date()).days


def usd_zh(amount: float) -> str:
    if abs(amount) >= 1e8:
        return f"{amount / 1e8:.2f} 亿美元"
    return f"{amount / 1e4:.0f} 万美元"


def cn_day(filed: str) -> str:
    y, m, d = filed[:10].split("-")
    return f"{y} 年 {int(m)} 月 {int(d)} 日"


def zh_lead(line: str) -> str:
    match = re.search(
        r"Total revenues for the (first|second|third|fourth) quarter of (\d{4}) were \$([\d,.]+) (million|billion), "
        r"compared to total revenues of \$([\d,.]+) (million|billion) for the (first|second|third|fourth) quarter of (\d{4}), "
        r"a ([\d.]+)% (increase|decrease)",
        line,
        re.I,
    )
    if not match:
        return line
    qmap = {"first": "第一", "second": "第二", "third": "第三", "fourth": "第四"}
    unit = {"million": 1e6, "billion": 1e9}

    def amount(num: str, name: str) -> str:
        return usd_zh(float(num.replace(",", "")) * unit[name.lower()])

    q1, y1, n1, u1, n2, u2, q2, y2, pct, direction = match.groups()
    verb = "增长" if direction.lower() == "increase" else "下降"
    return (
        f"{y1} 年{qmap[q1.lower()]}季度营收 {amount(n1, u1)}，"
        f"上年同期 {amount(n2, u2)}，同比{verb} {pct}%。"
    )


def build_news(row: dict[str, Any]) -> str:
    filed = str(row.get("filed") or "")
    name = str(row.get("name") or row.get("ticker"))
    ticker = str(row.get("ticker") or "")
    form = str(row.get("form") or "8-K")
    parts = [
        f"吴说获悉，{name}（{ticker}）于 {cn_day(filed)} 向美国证监会提交业绩文件（{form}）。"
    ]
    revenue = row.get("revenue_usd")
    period = str(row.get("revenue_period_end") or "")
    filed_day = datetime.strptime(filed[:10], "%Y-%m-%d")
    period_ok = False
    if period:
        try:
            gap = (filed_day - datetime.strptime(period[:10], "%Y-%m-%d")).days
            period_ok = 0 <= gap <= 50
        except ValueError:
            period_ok = False
    if isinstance(revenue, (int, float)) and period_ok:
        parts.append(
            f"SEC 财报口径该季（截至 {period}）营收约 {usd_zh(float(revenue))}，与新闻稿分部收入可能不一致。"
        )
    eps = row.get("eps_diluted")
    if isinstance(eps, (int, float)) and period_ok:
        parts.append(f"稀释每股收益约 {float(eps):.2f} 美元。")
    line = zh_lead(str(row.get("lead") or "").strip())
    low = line.lower()
    if line and not re.search(r"[\u4e00-\u9fff]", line):
        if "total net revenue" not in low and "total revenue" not in low:
            line = ""
    if line:
        parts.append(f"公告称：{line}")
    url = str(row.get("url") or "").strip()
    if url:
        parts.append(f"全文：{url}")
    return "".join(parts) + "\n" + PAGE


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
    state = load_state()
    seen = set(state["seen"])
    fresh: list[dict[str, Any]] = []
    stale: list[str] = []
    for row in doc["filings"]:
        if not isinstance(row, dict):
            continue
        acc = str(row.get("accession") or "")
        filed = str(row.get("filed") or "")
        if not acc or not filed:
            continue
        if acc in seen and not args.force:
            continue
        age = age_days(filed)
        if age is None:
            continue
        if age <= FRESH_DAYS:
            fresh.append(row)
        else:
            stale.append(acc)
    print(f"[crypto-earnings] fresh={len(fresh)} stale_unseen={len(stale)} seen={len(seen)}")
    for row in fresh:
        print(f"  FRESH {row.get('ticker')} {row.get('filed')} {row.get('form')}")
        print(build_news(row))
        print("---")
    if not args.push:
        print("[dry-run] 未推送")
        return 0
    for acc in stale:
        if acc not in seen:
            state["seen"].append(acc)
            seen.add(acc)
    if not fresh:
        save_state(state)
        print("[skip] 无 10 日内新业绩")
        return 0
    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    for row in fresh:
        acc = str(row["accession"])
        text = build_news(row)
        try:
            send_lark(webhook, secret, text)
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] Lark 失败 {row.get('ticker')}: {exc}", file=sys.stderr)
            save_state(state)
            return 1
        print(f"  → Lark OK {row.get('ticker')} {acc}")
        if acc not in seen:
            state["seen"].append(acc)
            seen.add(acc)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"=== {stamp} {acc}\n{text}\n\n")
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
