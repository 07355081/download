#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预测市场月名义成交创通道新高 → Lark 新闻。

读 prediction_markets_quarterly.json 的完整 UTC 月序列（DefiLlama 免费）。
只发最近一个已闭合月，且「合计」或「Kalshi」创本通道新高；
月结束后超过 10 天不发；不回扫旧月；首个完整月只建基线。

用法：
  python monthly_flash.py
  python monthly_flash.py --push
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
    HERE / "output" / "json" / "prediction_markets_quarterly.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "prediction_markets_quarterly.json",
    Path("/root/dashboard/public/json/prediction_markets_quarterly.json"),
]
STATE_PATH = HERE / "output" / "pm_monthly_vol_lark.json"
LOG_PATH = HERE / "monthly_flash.log"
PAGE = "https://data.wublock123.com/dashboard/prediction-markets"
TZ_CN = timezone(timedelta(hours=8))
FRESH_DAYS = 10
TEN_B = 10_000_000_000.0
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
            raise RuntimeError(result)


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


def resolve_json() -> Path:
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 prediction_markets_quarterly.json，请先跑 download.py")


def wan(usd: float) -> str:
    if abs(usd) >= 1e8:
        return f"{usd / 1e8:.2f} 亿美元"
    return f"{usd / 1e4:.0f} 万美元"


def cn_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y} 年 {m} 月"


def brand_vol(row: dict[str, Any], name: str) -> float:
    for protocol in row.get("protocols") or []:
        if isinstance(protocol, dict) and str(protocol.get("name") or "") == name:
            try:
                return float(protocol.get("volume_usd") or 0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def month_end(ym: str) -> date:
    y, m = int(ym[:4]), int(ym[5:7])
    return date(y, m, monthrange(y, m)[1])


def ten_b_streak(months: list[dict[str, Any]]) -> int:
    streak = 0
    for row in reversed(months):
        if float(row.get("volume_usd") or 0) >= TEN_B:
            streak += 1
        else:
            break
    return streak


def build_news(
    latest: dict[str, Any],
    *,
    total_ath: bool,
    kalshi_ath: bool,
    prior_total_month: str | None,
    prior_total: float | None,
    prior_kalshi_month: str | None,
    prior_kalshi: float | None,
    streak: int,
) -> str:
    total = float(latest["volume_usd"])
    kalshi = brand_vol(latest, "Kalshi")
    poly = brand_vol(latest, "Polymarket")
    hooks: list[str] = []
    if total_ath:
        prior = ""
        if prior_total_month and prior_total is not None:
            prior = f"高于此前最高的 {cn_month(prior_total_month)}（约 {wan(prior_total)}）。"
        hooks.append(f"合计约 {wan(total)}，创本通道有记录以来新高。{prior}")
    else:
        hooks.append(f"合计约 {wan(total)}。")
    if kalshi_ath:
        prior_k = ""
        if prior_kalshi_month and prior_kalshi is not None:
            prior_k = f"高于此前最高的 {cn_month(prior_kalshi_month)}（约 {wan(prior_kalshi)}）。"
        hooks.append(f"其中 Kalshi 约 {wan(kalshi)}，创本通道新高。{prior_k}")
    elif kalshi > 0:
        hooks.append(f"其中 Kalshi 约 {wan(kalshi)}。")
    if poly > 0:
        hooks.append(f"Polymarket 约 {wan(poly)}。")
    streak_txt = ""
    if streak >= 2 and total >= TEN_B:
        streak_txt = f"合计已连续 {streak} 个完整月超过 100 亿美元。"
    return (
        f"吴说获悉，据吴说数据中心统计，{cn_month(latest['month'])} 预测市场名义成交"
        f"{''.join(hooks)}{streak_txt}"
        f"口径为 DefiLlama Prediction Market 日名义成交按品牌合并后的 UTC 自然月；"
        f"名义成交不等于用户实付本金，与第三方全行业统计可能不一致。"
        f"\n{PAGE}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    try:
        path = resolve_json()
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    months = [
        m
        for m in (doc.get("months") or [])
        if isinstance(m, dict) and m.get("month") and float(m.get("volume_usd") or 0) > 0
    ]
    months.sort(key=lambda m: str(m["month"]))
    if len(months) < 2:
        print("[skip] 完整月不足", file=sys.stderr)
        return 0

    latest = months[-1]
    prior = months[:-1]
    total = float(latest["volume_usd"])
    kalshi = brand_vol(latest, "Kalshi")
    prior_total = max((float(m.get("volume_usd") or 0) for m in prior), default=None)
    prior_kalshi = max((brand_vol(m, "Kalshi") for m in prior), default=None)
    prior_total_month = None
    prior_kalshi_month = None
    if prior_total is not None:
        for m in prior:
            if abs(float(m.get("volume_usd") or 0) - prior_total) < 1.0:
                prior_total_month = str(m["month"])
                break
    if prior_kalshi is not None:
        for m in prior:
            if abs(brand_vol(m, "Kalshi") - prior_kalshi) < 1.0:
                prior_kalshi_month = str(m["month"])
                break

    total_ath = prior_total is None or total > prior_total + 1.0
    kalshi_ath = prior_kalshi is None or kalshi > prior_kalshi + 1.0
    is_ath = total_ath or kalshi_ath
    end = month_end(str(latest["month"]))
    age = (datetime.now(TZ_CN).date() - end).days
    fresh = 0 <= age <= FRESH_DAYS
    streak = ten_b_streak(months)

    print(
        f"[pm-monthly-vol] json={path} latest={latest['month']} "
        f"total={total:.0f} kalshi={kalshi:.0f} total_ath={total_ath} "
        f"kalshi_ath={kalshi_ath} streak10b={streak} age={age}d fresh={fresh}"
    )

    if not is_ath and not args.force:
        print("[skip] 最新完整月合计/Kalshi 均未创新高")
        return 0
    if not fresh and not args.force:
        print(f"[skip] 月结束已 {age} 天，超过 {FRESH_DAYS} 天（不补历史）")
        return 0
    if prior_total is None and not args.force:
        print("[skip] 首个完整月仅建基线，不推")
        return 0

    text = as_news(
        build_news(
            latest,
            total_ath=total_ath,
            kalshi_ath=kalshi_ath,
            prior_total_month=prior_total_month,
            prior_total=prior_total,
            prior_kalshi_month=prior_kalshi_month,
            prior_kalshi=prior_kalshi,
            streak=streak,
        )
    )
    msg_id = f"pm-month-vol-ath-{latest['month']}"
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
        print(f"[ERROR] Lark: {exc}", file=sys.stderr)
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
