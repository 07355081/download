#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""币安标注总资产骤降 → Lark 新闻体。

只看最新日。单日跌幅达到 8%，或两日跌幅达到 12%，才发。
发出前用 DefiLlama 同一字段 totalLiquidityUSD 再对被比较的日期。
本地与来源相对差超过 1%，或最新日仍是当天 UTC 日，不发。
不回扫，不把平时波动写成新闻，也不写成交易所倒闭。
口径是 DefiLlama 交易所标注资产，即看板储备序列，不是月度储备金证明。
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATE_PATH = HERE / "output" / "binance_assets_lark.json"
JSON_CANDIDATES = [
    Path("/root/dashboard/public/json/cex-asset-vol/cex_exchange_daily_assets_usd.json"),
    ROOT / "cex-asset&vol" / "output" / "json" / "cex_exchange_daily_assets_usd.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "cex-asset-vol" / "cex_exchange_daily_assets_usd.json",
]
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
PAGE = "https://data.wublock123.com/dashboard/binance-incident"
CHART = "https://data.wublock123.com/dashboard/cex-assets"
DROP_1D = -8.0
DROP_2D = -12.0
FRESH_DAYS = 3
SOURCE_URL = "https://api.llama.fi/protocol/binance-cex"
SOURCE_MISMATCH_PCT = 1.0

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
    if body and str(json.loads(body).get("code", "0")) != "0":
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


def load_points() -> list[tuple[str, float]]:
    for path in JSON_CANDIDATES:
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        rows = ((doc.get("data") or {}).get("rows") if isinstance(doc, dict) else None) or []
        points: list[tuple[str, float]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            day = str(row.get("date") or "")
            value = row.get("Binance")
            if day and isinstance(value, (int, float)) and float(value) > 0:
                points.append((day, float(value)))
        if len(points) >= 3:
            print(f"[binance-assets] json={path} n={len(points)}")
            return points
    raise FileNotFoundError("找不到币安标注资产序列")


def fmt_yi(value: float) -> str:
    return f"{value / 1e8:.2f} 亿美元"


def fmt_pct(value: float) -> str:
    return f"{'+' if value > 0 else ''}{value:.1f}%"


def cn_md(day: str) -> str:
    _year, month, date = day.split("-")
    return f"{int(month)}月{int(date)}日"


def pct(now: float, before: float) -> float | None:
    if before <= 0:
        return None
    return (now - before) / before * 100.0


def live_headline(days: set[str]) -> dict[str, float]:
    """DefiLlama 已汇总的 totalLiquidityUSD。同一 UTC 日取最晚一个点。"""
    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "dashboard-data/binance-assets-flash"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        doc = json.loads(resp.read().decode("utf-8"))
    best: dict[str, tuple[int, float]] = {}
    for item in doc.get("tvl") or []:
        if not isinstance(item, dict) or item.get("date") is None or item.get("totalLiquidityUSD") is None:
            continue
        day = datetime.fromtimestamp(int(item["date"]), tz=timezone.utc).date().isoformat()
        if day not in days:
            continue
        stamp = int(item["date"])
        prev = best.get(day)
        if prev is None or stamp > prev[0]:
            best[day] = (stamp, float(item["totalLiquidityUSD"]))
    return {day: value for day, (_stamp, value) in best.items()}


def source_gap(local: float, live: float) -> float | None:
    if live == 0:
        return None
    return abs(local - live) / abs(live) * 100.0


def source_ok(points: list[tuple[str, float]]) -> bool:
    """被比较的日期必须与来源现值相对差不超过 1%。读不到来源则不发。"""
    wanted = {day for day, _value in points[-3:]}
    try:
        live = live_headline(wanted)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
        print(f"[skip] 读不到 DefiLlama 现值: {exc}")
        return False
    for day, local in points[-3:]:
        remote = live.get(day)
        if remote is None:
            print(f"[skip] DefiLlama 现值没有 {day}")
            return False
        gap = source_gap(local, remote)
        print(f"[binance-assets] source {day} local={local:.0f} live={remote:.0f} gap={gap:.3f}%")
        if gap is None or gap > SOURCE_MISMATCH_PCT:
            print(f"[skip] {day} 与来源相对差超过 {SOURCE_MISMATCH_PCT:.0f}%")
            return False
    return True


def build_news(latest: tuple[str, float], prev: tuple[str, float], base: tuple[str, float] | None, avg: float, why: str) -> str:
    day, total = latest
    prev_day, prev_total = prev
    delta = total - prev_total
    word = "多" if delta >= 0 else "少"
    dod = pct(total, prev_total)
    lead_extra = ""
    versus_base = ""
    if base is not None:
        base_day, base_total = base
        two = pct(total, base_total)
        if isinstance(two, float):
            lead_extra = f"，较{cn_md(base_day)} {fmt_pct(two)}"
            versus_base = f"较{cn_md(base_day)}少 {fmt_yi(base_total - total)}（{fmt_pct(two)}），"
    paragraph = (
        f"{cn_md(day)}币安标注总资产降至 {fmt_yi(total)}{lead_extra}。"
        f"据吴说数据中心统计，当日币安标注总资产为 {fmt_yi(total)}，"
        f"较前一日（{cn_md(prev_day)}）{word} {fmt_yi(abs(delta))}"
        f"{f'（环比 {fmt_pct(dod)}）' if isinstance(dod, float) else ''}，"
        f"{versus_base}近 5 日均值 {fmt_yi(avg)}。"
        f"{why}"
        "这列就是看板上的交易所储备，口径为 DefiLlama 标注资产，不是币安月度储备金证明，也不等于已经确认的损失金额。"
    )
    return f"{paragraph}\n{PAGE}\n{CHART}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        points = load_points()
    except (OSError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    day, total = points[-1]
    prev_day, prev_total = points[-2]
    age = (datetime.now(timezone.utc).date() - datetime.strptime(day, "%Y-%m-%d").date()).days
    dod = pct(total, prev_total)
    base = points[-3] if len(points) >= 3 else None
    two = pct(total, base[1]) if base else None
    prior_one = [pct(points[i][1], points[i - 1][1]) for i in range(1, len(points) - 1)]
    prior_two = [pct(points[i][1], points[i - 2][1]) for i in range(2, len(points) - 1)]
    prior_one = [item for item in prior_one if isinstance(item, float)]
    prior_two = [item for item in prior_two if isinstance(item, float)]
    worst_one = isinstance(dod, float) and prior_one and dod < min(prior_one)
    worst_two = isinstance(two, float) and prior_two and two < min(prior_two)
    fire = (isinstance(dod, float) and dod <= DROP_1D) or (isinstance(two, float) and two <= DROP_2D)
    print(f"[binance-assets] day={day} total={total:.0f} dod={dod} two={two} worst1={worst_one} worst2={worst_two}")
    if age > FRESH_DAYS:
        print(f"[skip] {day} 过旧 age={age}")
        return 0
    if datetime.strptime(day, "%Y-%m-%d").date() >= datetime.now(timezone.utc).date():
        print(f"[skip] {day} 仍是当天 UTC 日，总数未完成")
        return 0
    if not fire and not args.force:
        print("[skip] 储备没有出现单日或两日骤降")
        return 0
    if worst_two and isinstance(two, float):
        why = f"这两日降幅为 {fmt_pct(two)}，大于有统计以来其余日期。"
    elif worst_one and isinstance(dod, float):
        why = f"单日降幅为 {fmt_pct(dod)}，大于有统计以来其余日期。"
    elif isinstance(two, float) and two <= DROP_2D:
        why = f"两日降幅达到 {fmt_pct(two)}。"
    elif isinstance(dod, float):
        why = f"单日降幅达到 {fmt_pct(dod)}。"
    else:
        why = ""
    if not source_ok(points):
        return 0
    window = [value for _day, value in points[-5:]]
    avg = sum(window) / len(window)
    text = build_news((day, total), (prev_day, prev_total), base, avg, why)
    msg_id = f"binance-assets-drop-{day}"
    print("---")
    print(msg_id)
    print(as_news(text))
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0
    state = load_state()
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
        return 0
    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    try:
        send_lark(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Lark 失败: {exc}", file=sys.stderr)
        return 1
    state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-60:]
    save_state(state)
    print(f"[pushed] {msg_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
