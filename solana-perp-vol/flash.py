#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GMTrade（Solana 永续）日成交 → JSON。日新高或近 7 日新高才发新闻。

成交来自该协议公开的日记录，一次拉全历史。不含 Solana 上其他永续协议。
当天未结束的 UTC 日不入库。第一次 --push 只记基线，不发已经过去的高点。
之后只看最新一个完整日：必须落在近 2 天内，且日成交或截至该日的近 7 日成交创序列新高。
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
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_PATH = HERE / "output" / "json" / "gmtrade_volume.json"
STATE_PATH = HERE / "output" / "gmtrade_volume_lark.json"
LOG_PATH = HERE / "flash.log"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/gmtrade-volume"
API = "https://gmx-solana-sqd.squids.live/gmx-solana-base:prod/api/graphql"
SCALE = 10**20
FRESH_DAYS = 2
MIN_DAYS = 60
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
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": as_news(text)}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = gen_sign(secret, ts)
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
        return {"sentIds": [], "bootstrapped": False}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": [], "bootstrapped": False}
    if not isinstance(doc, dict):
        return {"sentIds": [], "bootstrapped": False}
    ids = doc.get("sentIds")
    return {
        "sentIds": [item for item in ids if isinstance(item, str)] if isinstance(ids, list) else [],
        "bootstrapped": bool(doc.get("bootstrapped")),
    }


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def graphql(where: str) -> list[dict[str, Any]]:
    query = (
        "{ volumeRecordDailies("
        + where
        + " orderBy: timestamp_ASC, limit: 500) { timestamp tradeVolume } }"
    )
    payload = json.dumps({"query": query}).encode("utf-8")
    last: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                API,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "dashboard-data/gmtrade-volume",
                },
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                doc = json.loads(resp.read().decode("utf-8"))
            if doc.get("errors"):
                raise RuntimeError(doc["errors"])
            rows = (doc.get("data") or {}).get("volumeRecordDailies")
            if not isinstance(rows, list):
                raise RuntimeError("日成交记录缺失")
            return [row for row in rows if isinstance(row, dict)]
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
            last = exc
            if attempt == 2:
                raise
            time.sleep(1.5 + attempt)
    raise RuntimeError(last)


def fetch_days() -> dict[str, float]:
    rows: list[dict[str, Any]] = []
    cursor = ""
    for _ in range(20):
        where = f'where: {{timestamp_gt: "{cursor}"}}, ' if cursor else ""
        batch = graphql(where)
        if not batch:
            break
        rows.extend(batch)
        cursor = str(batch[-1].get("timestamp") or "")
        if len(batch) < 500 or not cursor:
            break
    if len(rows) < MIN_DAYS:
        raise RuntimeError(f"日成交只有 {len(rows)} 条")
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        stamp = str(row.get("timestamp") or "")
        if len(stamp) < 10:
            continue
        raw = row.get("tradeVolume")
        try:
            usd = int(raw) / SCALE
        except (TypeError, ValueError):
            continue
        if usd < 0:
            continue
        totals[stamp[:10]] += usd
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {day: value for day, value in totals.items() if day < today}


def save_doc(days: dict[str, float]) -> None:
    ordered = sorted(days)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    latest = ordered[-1]
    peak = max(ordered, key=lambda day: days[day])
    doc = {
        "name": "GMTrade 日成交",
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest_day": latest,
        "latest_volume_usd": round(days[latest], 2),
        "peak_day": peak,
        "peak_volume_usd": round(days[peak], 2),
        "series": [
            {
                "label": "GMTrade",
                "months": [{"month": day, "usd": round(days[day], 2)} for day in ordered],
            }
        ],
    }
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)


def yi(usd: float) -> str:
    return f"{usd / 1e8:.1f} 亿美元"


def cn_day(day: str) -> str:
    year, month, dom = day.split("-")
    return f"{int(year)}年{int(month)}月{int(dom)}日"


def window_sum(ordered: list[str], days: dict[str, float], end: int) -> float:
    return sum(days[ordered[index]] for index in range(end - 6, end + 1))


def build_news(day: str, daily: float, week: float, daily_high: bool, week_high: bool) -> str:
    bits = [f"吴说获悉，据吴说数据中心统计，Solana 永续协议 GMTrade 在{cn_day(day)}成交约 {yi(daily)}"]
    if daily_high:
        bits[0] += "，创日成交新高"
    bits[0] += "。"
    week_bit = f"截至该日的近 7 日成交约 {yi(week)}"
    if week_high:
        week_bit += "，创近 7 日新高"
    bits.append(week_bit + "。")
    return "".join(bits) + f"\n{PAGE}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        days = fetch_days()
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] 拉数失败: {exc}", file=sys.stderr)
        return 1
    ordered = sorted(days)
    if len(ordered) < MIN_DAYS:
        print(f"[skip] 完整日不足 {len(ordered)}")
        return 0
    save_doc(days)
    latest = ordered[-1]
    age = (datetime.now(timezone.utc).date() - datetime.strptime(latest, "%Y-%m-%d").date()).days
    prior_daily = [days[day] for day in ordered[:-1]]
    daily_high = days[latest] > max(prior_daily) + 1
    week = window_sum(ordered, days, len(ordered) - 1)
    prior_weeks = [window_sum(ordered, days, index) for index in range(6, len(ordered) - 1)]
    week_high = bool(prior_weeks) and week > max(prior_weeks) + 1
    print(
        f"[gmtrade-volume] day={latest} vol={days[latest]/1e8:.2f}亿 "
        f"week={week/1e8:.2f}亿 daily_high={daily_high} week_high={week_high} age={age}d"
    )
    state = load_state()
    first = False
    if args.push and not state["bootstrapped"] and age <= FRESH_DAYS and not args.force:
        state["bootstrapped"] = True
        state["baselineDay"] = latest
        save_state(state)
        first = True
        print(f"[bootstrap] 已记基线 {latest}，这次不发")
    if age > FRESH_DAYS and not args.force:
        print("[skip] 最新完整日已超过 2 天")
        return 0
    if not daily_high and not week_high and not args.force:
        print("[skip] 未创日新高，近 7 日也未创新高")
        return 0
    text = build_news(latest, days[latest], week, daily_high, week_high)
    msg_id = f"gmtrade-volume-{latest}"
    print("---")
    print(f"id={msg_id}")
    print(as_news(text))
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0
    if first:
        print(f"[bootstrap] 已记基线 {latest}，不发已有高点")
        return 0
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
        return 0
    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
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
    state["sentIds"] = state["sentIds"][-200:]
    save_state(state)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} {msg_id} ok=True\n{text}\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
