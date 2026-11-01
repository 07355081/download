#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主要交易所现货月成交 → Lark 新闻。

读看板已有的月度 JSON。只发最近一个已结束月，且月末距今不超过 40 天，
环比绝对值达到 12% 才发。不回扫更早月份。

用法：
  python flash.py
  python flash.py --push
"""
from __future__ import annotations

import argparse
import base64
import calendar
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
STATE_PATH = HERE / "output" / "cex_spot_month_lark.json"
LOG_PATH = HERE / "flash.log"
PAGE_URL = "https://data.wublock123.com/dashboard/exchange_volume"
JSON_URL = "https://data.wublock123.com/json/cex-asset-vol/cex_exchange_monthly_spot_volume_usd.json"
JSON_CANDIDATES = [
    Path("/root/dashboard/public/json/cex-asset-vol/cex_exchange_monthly_spot_volume_usd.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "cex-asset-vol" / "cex_exchange_monthly_spot_volume_usd.json",
]
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
NOTABLE_PCT = 12.0
FRESH_DAYS = 40
UA = "dashboard-data/cex-spot-month"

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


def load_doc() -> dict[str, Any]:
    for path in JSON_CANDIDATES:
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                print(f"[cex-spot-month] json={path}")
                return doc
    req = urllib.request.Request(JSON_URL, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        doc = json.loads(resp.read().decode("utf-8"))
    print(f"[cex-spot-month] json={JSON_URL}")
    return doc


def num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def month_end(ym: str) -> date:
    year, month = int(ym[:4]), int(ym[5:7])
    return date(year, month, calendar.monthrange(year, month)[1])


def yi(usd: float) -> str:
    return f"{usd / 1e8:,.0f} 亿美元"


def cn_month(ym: str) -> str:
    return f"{int(ym[:4])} 年 {int(ym[5:7])} 月"


def build_news(
    curr: str,
    prev: str,
    curr_total: float,
    prev_total: float,
    mom: float,
    ranked: list[tuple[str, float, float]],
    top3_share: float,
    prev_top3_share: float,
    sample: int,
    all_up: bool,
) -> str:
    move = "上升" if mom >= 0 else "下降"
    names = "、".join(
        f"{name} {yi(val)}（占比 {share:.1f}%）" for name, val, share in ranked[:3]
    )
    breadth = "全部环比上涨" if all_up else "并非全部上涨"
    return (
        "【历史阈值警报】\n"
        f"据吴说数据中心统计，{cn_month(curr)}主要交易所现货成交量合计约 {yi(curr_total)}，"
        f"相较 {cn_month(prev)}的 {yi(prev_total)}{move}约 {abs(mom):.1f}%。"
        f"样本覆盖 {sample} 家交易所，{breadth}。"
        f"按成交额排名，前三为 {names}。"
        f"前三合计占比约 {top3_share:.1f}%，{cn_month(prev)}为 {prev_top3_share:.1f}%。"
        f"\n{PAGE_URL}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        doc = load_doc()
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    data = doc.get("data") if isinstance(doc, dict) else None
    rows = data.get("rows") if isinstance(data, dict) else None
    exchanges = data.get("exchanges") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not isinstance(exchanges, list):
        print("[ERROR] 月度 JSON 结构不对", file=sys.stderr)
        return 1
    venues = [str(name) for name in exchanges if str(name) != "ALL"]
    by_month: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ym = str(row.get("month") or "")[:7]
        if len(ym) == 7:
            by_month[ym] = row
    today = datetime.now(timezone.utc).date()
    current = f"{today.year:04d}-{today.month:02d}"
    closed = sorted(ym for ym in by_month if ym < current)
    if len(closed) < 2:
        print("[skip] 完整月不足两个")
        return 0
    curr, prev = closed[-1], closed[-2]
    age = (today - month_end(curr)).days
    curr_row, prev_row = by_month[curr], by_month[prev]
    curr_total = num(curr_row.get("ALL")) or sum(num(curr_row.get(name)) for name in venues)
    prev_total = num(prev_row.get("ALL")) or sum(num(prev_row.get(name)) for name in venues)
    if prev_total <= 0 or curr_total <= 0:
        print("[skip] 合计为空")
        return 0
    mom = (curr_total / prev_total - 1.0) * 100.0
    paired: list[tuple[str, float, float]] = []
    for name in venues:
        now_v = num(curr_row.get(name))
        then_v = num(prev_row.get(name))
        if now_v <= 0 and then_v <= 0:
            continue
        paired.append((name, now_v, then_v))
    ranked = sorted(((name, now_v, 100.0 * now_v / curr_total) for name, now_v, _ in paired), key=lambda item: -item[1])
    prev_ranked = sorted(paired, key=lambda item: -item[2])[:3]
    top3_share = sum(share for _, _, share in ranked[:3])
    prev_top3_share = 100.0 * sum(then_v for _, _, then_v in prev_ranked) / prev_total
    all_up = bool(paired) and all(now_v > then_v for _, now_v, then_v in paired if then_v > 0)
    print(
        f"[cex-spot-month] {curr} total={curr_total:.0f} mom={mom:.1f} age={age}d "
        f"top3={top3_share:.1f} sample={len(paired)}"
    )
    if age > FRESH_DAYS and not args.force:
        print(f"[skip] 月末已过 {age} 天")
        return 0
    if abs(mom) < NOTABLE_PCT and not args.force:
        print(f"[skip] 环比 {mom:.1f}% 未到 {NOTABLE_PCT:.0f}%")
        return 0
    text = build_news(
        curr, prev, curr_total, prev_total, mom, ranked, top3_share, prev_top3_share, len(paired), all_up
    )
    msg_id = f"cex-spot-month-{curr}"
    print("---")
    print(f"id={msg_id}")
    print(as_news(text))
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
            handle.write(f"=== {stamp} {msg_id} ok=False {exc}\n")
        return 1
    print(f"[pushed] {msg_id}")
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-80:]
    save_state(state)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} {msg_id} ok=True\n{as_news(text)}\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
