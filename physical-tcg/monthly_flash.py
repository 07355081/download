#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Physical TCG 月协议费用创序列新高 → Lark 新闻。

DefiLlama 免费：overview/fees 取 category=Physical TCG 成员，
summary/fees 日序列按 UTC 自然月加总。
只发最近一个已闭合月、且月合计创本通道新高；月结束后超过 10 天不发；不回扫旧月。

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
import urllib.parse
import urllib.request
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DAY_JSON = HERE / "output" / "json" / "physical_tcg.json"
OUT_PATH = HERE / "output" / "json" / "physical_tcg_monthly.json"
STATE_PATH = HERE / "output" / "physical_tcg_monthly_lark.json"
LOG_PATH = HERE / "monthly_flash.log"
JSON_URL = "https://data.wublock123.com/dashboard/lark-feeds/physical-tcg"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/lark-feeds/physical-tcg"
API = "https://api.llama.fi"
CATEGORY = "Physical TCG"
UA = "dashboard-data/physical-tcg-monthly"
TZ_CN = timezone(timedelta(hours=8))
FRESH_DAYS = 10
TOP_N = 5
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
    last: Exception | None = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last = exc
            if attempt < 3:
                time.sleep(min(2**attempt, 8))
    raise RuntimeError(f"GET {url} failed: {last}")


def wan(usd: float) -> str:
    if abs(usd) >= 1e8:
        return f"{usd / 1e8:.2f} 亿美元"
    return f"{usd / 1e4:.0f} 万美元"


def cn_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y} 年 {m} 月"


def load_day_store() -> dict[str, dict[str, float]]:
    if not DAY_JSON.is_file():
        return {}
    try:
        doc = json.loads(DAY_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = doc.get("days") if isinstance(doc, dict) else None
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, float]] = {}
    for day, row in raw.items():
        if not isinstance(day, str) or not isinstance(row, dict):
            continue
        cleaned = {
            str(slug): float(val)
            for slug, val in row.items()
            if isinstance(slug, str) and isinstance(val, (int, float))
        }
        if cleaned:
            out[day] = cleaned
    return out


def fetch_days(slugs: set[str], seed: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    days = {k: dict(v) for k, v in seed.items()}
    for slug in sorted(slugs):
        url = API + "/summary/fees/" + urllib.parse.quote(slug, safe=".-_")
        doc = http_json(url)
        for pt in doc.get("totalDataChart") or []:
            if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                continue
            try:
                day = datetime.fromtimestamp(int(pt[0]), tz=timezone.utc).strftime("%Y-%m-%d")
                val = float(pt[1] or 0)
            except (TypeError, ValueError, OSError):
                continue
            days.setdefault(day, {})[slug] = val
        time.sleep(0.12)
    return days


def complete_months(
    days: dict[str, dict[str, float]], slugs: set[str]
) -> list[dict[str, Any]]:
    by_m_total: dict[str, float] = defaultdict(float)
    by_m_parts: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    by_m_days: dict[str, set[str]] = defaultdict(set)
    for day, row in days.items():
        ym = day[:7]
        by_m_days[ym].add(day)
        for slug, val in row.items():
            if slug not in slugs:
                continue
            by_m_total[ym] += val
            by_m_parts[ym][slug] += val
    out: list[dict[str, Any]] = []
    for ym in sorted(by_m_total):
        y, m = int(ym[:4]), int(ym[5:7])
        need = monthrange(y, m)[1]
        if len(by_m_days[ym]) < need:
            continue
        parts = {k: round(v, 2) for k, v in by_m_parts[ym].items()}
        out.append(
            {
                "month": ym,
                "fees_usd": round(by_m_total[ym], 2),
                "parts": parts,
                "month_end": date(y, m, need).isoformat(),
            }
        )
    return out


def build_news(
    row: dict[str, Any],
    names: dict[str, str],
    prior_month: str | None,
    prior_high: float | None,
    streak: int,
) -> str:
    ranked = sorted(row["parts"].items(), key=lambda kv: -kv[1])[:TOP_N]
    bits = [f"{names.get(slug, slug)} 约 {wan(val)}" for slug, val in ranked if val > 0]
    detail = "、".join(bits)
    prior = ""
    if prior_month and prior_high is not None:
        prior = f"高于此前最高的 {cn_month(prior_month)}（约 {wan(prior_high)}）。"
    streak_txt = ""
    if streak >= 2:
        streak_txt = f"已连续 {streak} 个完整月创本通道新高。"
    return (
        f"吴说获悉，据吴说数据中心统计，{cn_month(row['month'])} Physical TCG 赛道协议费用合计约 {wan(float(row['fees_usd']))}，"
        f"创本通道有记录以来新高。{prior}{streak_txt}"
        f"其中 {detail}。"
        f"口径为 DefiLlama 免费协议费用日序列，按当前 Physical TCG 类别成员汇总，UTC 自然月。"
        f"\n{DASHBOARD_URL}"
    )


def ath_streak(months: list[dict[str, Any]]) -> int:
    """连续创本通道新高的完整月数（以序列末月结尾；末月非 ATH 则 0）。"""
    if not months:
        return 0
    ath: float | None = None
    streak = 0
    for row in months:
        val = float(row["fees_usd"])
        if ath is None or val > ath + 1.0:
            ath = val
            streak += 1
        else:
            streak = 0
    return streak


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-fetch", action="store_true", help="只用已有 physical_tcg.json 日表")
    args = parser.parse_args()

    try:
        overview = http_json(
            API + "/overview/fees?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true"
        )
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    protocols = [
        p
        for p in (overview.get("protocols") or [])
        if isinstance(p, dict) and p.get("category") == CATEGORY and isinstance(p.get("slug"), str)
    ]
    if not protocols:
        print("[ERROR] Physical TCG 名单为空", file=sys.stderr)
        return 1
    names = {
        p["slug"]: str(p.get("displayName") or p.get("name") or p["slug"]) for p in protocols
    }
    slugs = set(names)

    seed = load_day_store()
    try:
        days = seed if args.skip_fetch and seed else fetch_days(slugs, seed)
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    months = complete_months(days, slugs)
    if not months:
        print("[skip] 无完整月", file=sys.stderr)
        return 0

    latest = months[-1]
    prior = months[:-1]
    prior_high = max((float(m["fees_usd"]) for m in prior), default=None)
    prior_month = None
    if prior_high is not None:
        for m in prior:
            if abs(float(m["fees_usd"]) - prior_high) < 1e-6:
                prior_month = m["month"]
    is_ath = prior_high is None or float(latest["fees_usd"]) > prior_high + 1.0
    end = date.fromisoformat(latest["month_end"])
    age = (datetime.now(TZ_CN).date() - end).days
    fresh = age <= FRESH_DAYS
    streak = ath_streak(months) if is_ath else 0

    payload = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "DefiLlama overview/fees + summary/fees",
        "category": CATEGORY,
        "latest_month": latest["month"],
        "latest_fees_usd": latest["fees_usd"],
        "is_ath": is_ath,
        "ath_streak": streak,
        "age_days": age,
        "top": [
            {"slug": s, "name": names.get(s, s), "fees_usd": round(v, 2)}
            for s, v in sorted(latest["parts"].items(), key=lambda kv: -kv[1])[:12]
        ],
        "months": [
            {
                "month": m["month"],
                "fees_usd": m["fees_usd"],
                "top": [
                    {"slug": s, "name": names.get(s, s), "fees_usd": round(v, 2)}
                    for s, v in sorted(m["parts"].items(), key=lambda kv: -kv[1])[:8]
                ],
            }
            for m in months
        ],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)

    print(
        f"[physical-tcg-monthly] latest={latest['month']} fees={latest['fees_usd']:.0f} "
        f"ath={is_ath} streak={streak} age={age}d fresh={fresh}"
    )

    if not is_ath and not args.force:
        print("[skip] 最新完整月未创费用新高")
        return 0
    if not fresh and not args.force:
        print(f"[skip] 月结束已 {age} 天，超过 {FRESH_DAYS} 天（不补历史）")
        return 0
    if prior_high is None and not args.force:
        print("[skip] 首个完整月仅建基线，不推")
        return 0

    text = as_news(build_news(latest, names, prior_month, prior_high, streak))
    msg_id = f"physical-tcg-month-ath-{latest['month']}"
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
