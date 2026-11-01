#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hyper Foundation HYPE Staking 日费用 → JSON。日费用创序列新高才发新闻。

免费接口：DefiLlama summary/fees/hyper-foundation-hype-staking。
当天未结束的 UTC 日不入库。第一次 --push 只记基线，不发已有高点。
之后只看最新完整日：必须落在近 2 天内，且日费用严格高于此前所有完整日。
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_PATH = HERE / "output" / "json" / "hype_staking_fees.json"
STATE_PATH = HERE / "output" / "hype_staking_fees_lark.json"
LOG_PATH = HERE / "flash.log"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/hype-staking-fees"
API = "https://api.llama.fi/summary/fees/hyper-foundation-hype-staking?dataType=dailyFees"
FRESH_DAYS = 2
MIN_DAYS = 30
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


def http_json(url: str) -> Any:
    last: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "dashboard-data/hype-staking-fees", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            if attempt == 2:
                raise
            time.sleep(1.5 + attempt)
    raise RuntimeError(last)


def fetch_rows() -> list[dict[str, Any]]:
    doc = http_json(API)
    chart = doc.get("totalDataChart") if isinstance(doc, dict) else None
    if not isinstance(chart, list) or len(chart) < MIN_DAYS:
        raise RuntimeError("日费用序列不足")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows: list[dict[str, Any]] = []
    for point in chart:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        ts, value = point[0], point[1]
        if not isinstance(ts, (int, float)) or not isinstance(value, (int, float)):
            continue
        day = datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d")
        if day >= today:
            continue
        rows.append({"date": day, "fees_usd": float(value)})
    rows.sort(key=lambda item: str(item["date"]))
    if len(rows) < MIN_DAYS:
        raise RuntimeError(f"完整日只有 {len(rows)} 条")
    return rows


def save_doc(rows: list[dict[str, Any]]) -> None:
    latest = rows[-1]
    peak = max(rows, key=lambda item: float(item["fees_usd"]))
    last7 = rows[-7:]
    doc = {
        "name": "Hyper Foundation HYPE Staking 日费用",
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest": latest,
        "peak": {"date": peak["date"], "fees_usd": peak["fees_usd"]},
        "last7_usd": round(sum(float(item["fees_usd"]) for item in last7), 2),
        "row_count": len(rows),
        "data": rows,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)


def wan(usd: float) -> str:
    if usd >= 1e6:
        return f"{usd / 1e4:.0f} 万美元"
    return f"{usd / 1e4:.1f} 万美元"


def cn_day(day: str) -> str:
    year, month, dom = day.split("-")
    return f"{int(year)}年{int(month)}月{int(dom)}日"


def build_news(day: str, fees: float, peak_prev: float) -> str:
    return (
        f"吴说获悉，据吴说数据中心统计，Hyper Foundation HYPE Staking 在{cn_day(day)}"
        f"费用约 {wan(fees)}，创日费用新高。"
        f"此前高点约 {wan(peak_prev)}。"
        f"该费用指委托 HYPE 质押产生的收益。"
        f"\n{PAGE}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        rows = fetch_rows()
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] 拉数失败: {exc}", file=sys.stderr)
        return 1
    save_doc(rows)
    latest = rows[-1]
    day = str(latest["date"])
    fees = float(latest["fees_usd"])
    age = (datetime.now(timezone.utc).date() - datetime.strptime(day, "%Y-%m-%d").date()).days
    prior = [float(row["fees_usd"]) for row in rows[:-1]]
    ath = fees > max(prior) + 1
    peak_prev = max(prior) if prior else 0.0
    print(f"[hype-staking-fees] day={day} fees={fees/1e4:.1f}万 ath={ath} peak_prev={peak_prev/1e4:.1f}万 age={age}d")

    state = load_state()
    first = False
    if args.push and not state["bootstrapped"] and not args.force:
        state["bootstrapped"] = True
        state["baselineDay"] = day
        save_state(state)
        first = True
        print(f"[bootstrap] 已记基线 {day}，这次不发")

    if age > FRESH_DAYS and not args.force:
        print("[skip] 最新完整日已超过 2 天")
        return 0
    if not ath and not args.force:
        print("[skip] 未创日费用新高")
        return 0

    text = build_news(day, fees, peak_prev)
    msg_id = f"hype-staking-fees-{day}"
    print("---")
    print(f"id={msg_id}")
    print(as_news(text))
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0
    if first:
        print(f"[bootstrap] 已记基线 {day}，不发已有高点")
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
