#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主要交易所衍生品月成交与期现比 → Lark 新闻。

读看板已有的月度 JSON。只发最近一个已结束月，且月末距今不超过 40 天。
成交额环比或期现比环比的绝对值达到 12% 才发。不回扫更早月份。

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
STATE_PATH = HERE / "output" / "cex_futures_month_lark.json"
LOG_PATH = HERE / "flash.log"
PAGE_URL = "https://data.wublock123.com/dashboard/exchange_volume"
FUT_URL = "https://data.wublock123.com/json/cex-asset-vol/cex_exchange_monthly_futures_volume_usd.json"
SPOT_URL = "https://data.wublock123.com/json/cex-asset-vol/cex_exchange_monthly_spot_volume_usd.json"
FUT_CANDIDATES = [
    Path("/root/dashboard/public/json/cex-asset-vol/cex_exchange_monthly_futures_volume_usd.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "cex-asset-vol" / "cex_exchange_monthly_futures_volume_usd.json",
]
SPOT_CANDIDATES = [
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
UA = "dashboard-data/cex-futures-month"

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


def load_doc(candidates: list[Path], url: str) -> dict[str, Any]:
    for path in candidates:
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                print(f"[cex-futures-month] json={path.name}")
                return doc
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        print(f"[cex-futures-month] json={url}")
        return json.loads(resp.read().decode("utf-8"))


def num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def by_month(doc: dict[str, Any]) -> tuple[list[str], dict[str, dict[str, Any]]]:
    data = doc.get("data") if isinstance(doc, dict) else None
    rows = data.get("rows") if isinstance(data, dict) else None
    exchanges = data.get("exchanges") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not isinstance(exchanges, list):
        raise RuntimeError("月度 JSON 结构不对")
    venues = [str(name) for name in exchanges if str(name) != "ALL"]
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ym = str(row.get("month") or "")[:7]
        if len(ym) == 7:
            out[ym] = row
    return venues, out


def month_end(ym: str) -> date:
    year, month = int(ym[:4]), int(ym[5:7])
    return date(year, month, calendar.monthrange(year, month)[1])


def money(usd: float) -> str:
    if abs(usd) >= 1e12:
        return f"{usd / 1e12:.2f} 万亿美元"
    return f"{usd / 1e8:,.0f} 亿美元"


def cn_month(ym: str) -> str:
    return f"{int(ym[:4])} 年 {int(ym[5:7])} 月"


def total_of(row: dict[str, Any], venues: list[str]) -> float:
    all_value = num(row.get("ALL"))
    return all_value if all_value > 0 else sum(num(row.get(name)) for name in venues)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        fut = load_doc(FUT_CANDIDATES, FUT_URL)
        spot = load_doc(SPOT_CANDIDATES, SPOT_URL)
        venues, fut_months = by_month(fut)
        _, spot_months = by_month(spot)
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    today = datetime.now(timezone.utc).date()
    current = f"{today.year:04d}-{today.month:02d}"
    closed = sorted(ym for ym in fut_months if ym < current and ym in spot_months)
    if len(closed) < 2:
        print("[skip] 完整月不足两个")
        return 0
    curr, prev = closed[-1], closed[-2]
    age = (today - month_end(curr)).days
    curr_row, prev_row = fut_months[curr], fut_months[prev]
    curr_total = total_of(curr_row, venues)
    prev_total = total_of(prev_row, venues)
    if curr_total <= 0 or prev_total <= 0:
        print("[skip] 合计为空")
        return 0
    mom = (curr_total / prev_total - 1.0) * 100.0
    paired: list[tuple[str, float, float]] = []
    up = down = 0
    for name in venues:
        now_v = num(curr_row.get(name))
        then_v = num(prev_row.get(name))
        if now_v <= 0 and then_v <= 0:
            continue
        paired.append((name, now_v, then_v))
        if then_v > 0 and now_v > then_v:
            up += 1
        elif then_v > 0 and now_v < then_v:
            down += 1
    ranked = sorted(paired, key=lambda item: -item[1])
    prev_top = sorted(paired, key=lambda item: -item[2])[:3]
    top3_share = 100.0 * sum(now_v for _, now_v, _ in ranked[:3]) / curr_total
    prev_top3_share = 100.0 * sum(then_v for _, _, then_v in prev_top) / prev_total
    spot_curr = total_of(spot_months[curr], venues)
    spot_prev = total_of(spot_months[prev], venues)
    ratio_curr = curr_total / spot_curr if spot_curr > 0 else None
    ratio_prev = prev_total / spot_prev if spot_prev > 0 else None
    ratio_mom = None
    if ratio_curr and ratio_prev:
        ratio_mom = (ratio_curr / ratio_prev - 1.0) * 100.0
    print(
        f"[cex-futures-month] {curr} total={curr_total:.0f} mom={mom:.1f} "
        f"ratio={None if ratio_curr is None else round(ratio_curr, 2)} "
        f"ratio_mom={None if ratio_mom is None else round(ratio_mom, 1)} age={age}d"
    )
    fired = abs(mom) >= NOTABLE_PCT or (ratio_mom is not None and abs(ratio_mom) >= NOTABLE_PCT)
    if age > FRESH_DAYS and not args.force:
        print(f"[skip] 月末已过 {age} 天")
        return 0
    if not fired and not args.force:
        print(f"[skip] 成交额环比 {mom:.1f}% 、期现比未到 {NOTABLE_PCT:.0f}%")
        return 0

    move = "上升" if mom >= 0 else "下降"
    names = "、".join(
        f"{name} {money(now_v)}（占比 {100.0 * now_v / curr_total:.1f}%）"
        for name, now_v, _ in ranked[:3]
    )
    ratio_bit = ""
    if ratio_curr is not None and ratio_prev is not None and ratio_mom is not None:
        ratio_move = "上升" if ratio_mom >= 0 else "下降"
        ratio_bit = (
            f"合约/现货成交量比值 {cn_month(curr)}为 {ratio_curr:.2f} 倍，"
            f"{cn_month(prev)}为 {ratio_prev:.2f} 倍，{ratio_move}约 {abs(ratio_mom):.1f}%。"
        )
    text = (
        "【历史阈值警报】\n"
        f"据吴说数据中心统计，{cn_month(curr)}主要交易所衍生品成交量合计约 {money(curr_total)}，"
        f"相较 {cn_month(prev)}的 {money(prev_total)}{move}约 {abs(mom):.1f}%。"
        f"样本覆盖 {len(paired)} 家交易所，其中环比上涨 {up} 家、下跌 {down} 家。"
        f"按成交额排名，前三为 {names}。"
        f"前三合计占比约 {top3_share:.1f}%，{cn_month(prev)}为 {prev_top3_share:.1f}%。"
        f"{ratio_bit}"
        f"\n{PAGE_URL}"
    )
    msg_id = f"cex-futures-month-{curr}"
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
