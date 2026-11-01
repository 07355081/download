#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Collector Crypt 月协议收入 ATH → Lark 新闻体（DefiLlama 近似）。

数据优先读 protocol-revenue-ytd 已拉的 dailyRevenue；没有则现拉 DefiLlama。
只对「最近一个已闭合 UTC 自然月」判断是否创本通道月收入新高；首月建基线不推；不回扫旧月。
非 zkayAPE 扭蛋/卡包/毛利口径。

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
import urllib.error
import urllib.request
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SLUG = "collector-crypt"
YTD_CANDIDATES = [
    ROOT / "protocol-revenue-ytd" / "output" / "json" / "protocol_revenue_ytd.json",
    Path("/root/data-download/protocol-revenue-ytd/output/json/protocol_revenue_ytd.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "protocol-revenue-ytd" / "protocol_revenue_ytd.json",
]
OUT_PATH = HERE / "output" / "json" / "collector_crypt_monthly.json"
STATE_PATH = HERE / "output" / "collector_crypt_lark.json"
LOG_PATH = HERE / "flash.log"
JSON_URL = "https://data.wublock123.com/dashboard/lark-feeds/collector-crypt"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/lark-feeds/collector-crypt"
API = "https://api.llama.fi"
UA = "dashboard-data/collector-crypt"
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


def gen_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
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
    return {"sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else []}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def http_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_daily_from_llama(doc: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    chart = doc.get("totalDataChart") if isinstance(doc, dict) else None
    if not isinstance(chart, list):
        return out
    for pt in chart:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        ts, val = pt[0], pt[1]
        if isinstance(ts, (int, float)) and isinstance(val, (int, float)):
            day = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
            out[day] = float(val)
    return out


def load_daily() -> tuple[dict[str, float], str]:
    for path in YTD_CANDIDATES:
        if not path.is_file():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for row in doc.get("protocols") or []:
            if isinstance(row, dict) and str(row.get("slug")) == SLUG:
                daily = row.get("daily") or {}
                if isinstance(daily, dict) and daily:
                    print(f"[collector-crypt] daily ← {path}")
                    return {str(k): float(v) for k, v in daily.items() if isinstance(v, (int, float))}, str(
                        path
                    )
    url = f"{API}/summary/fees/{SLUG}?dataType=dailyRevenue"
    print(f"[collector-crypt] daily ← {url}")
    doc = http_json(url)
    return parse_daily_from_llama(doc), url


def month_end(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    last = monthrange(y, m)[1]
    return f"{ym}-{last:02d}"


def last_closed_month(today: datetime) -> str:
    first = today.replace(day=1)
    prev = first - timedelta(days=1)
    return prev.strftime("%Y-%m")


def monthly_totals(daily: dict[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for day, val in daily.items():
        if len(day) < 7:
            continue
        ym = day[:7]
        out[ym] = out.get(ym, 0.0) + float(val)
    return out


def yi(usd: float) -> str:
    if abs(usd) >= 1e8:
        return f"{usd / 1e8:.2f} 亿美元"
    return f"{usd / 1e4:.0f} 万美元"


def build_news(ym: str, rev: float, prior_high: float | None) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    prior_bit = ""
    if prior_high is not None:
        prior_bit = f"高于本通道已记录的最高月收入 {yi(prior_high)}。"
    return (
        f"据吴说数据中心统计，{y} 年 {m} 月 Collector Crypt 协议收入约 {yi(rev)}，"
        f"创本通道记录新高。{prior_bit}"
        f"口径为 DefiLlama dailyRevenue 自然月加总近似，不是 zkayAPE 扭蛋消费/二级成交/毛利口径；"
        f"数字可能与原文略有差异。"
        f"\n{DASHBOARD_URL}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    try:
        daily, source = load_daily()
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] 拉数失败: {exc}", file=sys.stderr)
        return 1
    if not daily:
        print("[ERROR] 无日收入序列", file=sys.stderr)
        return 1

    today = datetime.now(timezone.utc).date()
    closed = last_closed_month(datetime.now(timezone.utc))
    months = monthly_totals(daily)
    # 只保留已闭合月
    closed_months = {ym: v for ym, v in months.items() if month_end(ym) < today.isoformat()}
    if closed not in closed_months:
        print(f"[skip] 最近闭合月 {closed} 尚无数据")
        return 0

    rev = float(closed_months[closed])
    prior = [float(v) for ym, v in closed_months.items() if ym < closed]
    prior_high = max(prior) if prior else None
    is_ath = prior_high is not None and rev > prior_high + 1e-6

    series = [
        {"month": ym, "revenue_usd": round(float(v), 2)}
        for ym, v in sorted(closed_months.items())
    ]
    payload = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "slug": SLUG,
        "note": "DefiLlama dailyRevenue 自然月加总。非 zkayAPE 扭蛋/卡包/毛利。",
        "latest_closed_month": closed,
        "latest_revenue_usd": round(rev, 2),
        "prior_high_usd": round(prior_high, 2) if prior_high is not None else None,
        "is_ath": is_ath,
        "months": series[-24:],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(f"[collector-crypt] month={closed} rev={rev:.0f} prior_high={prior_high} ath={is_ath}")

    if not is_ath and not args.force:
        print("[skip] 未创月收入历史新高" if prior_high is not None else "[skip] 首月建基线，不推")
        return 0

    msg_id = f"collector-crypt-month-ath-{closed}"
    text = as_news(build_news(closed, rev, prior_high))
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
    state["sentIds"] = state["sentIds"][-120:]
    save_state(state)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} {msg_id} ok=True\n{text}\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
