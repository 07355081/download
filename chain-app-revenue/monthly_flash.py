#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""各链应用收入自然月排名 → Lark 新闻体。

DefiLlama overview/fees/{chain}?dataType=dailyRevenue 日序列加总（免费）。
只发最近一个已闭合月；月结束后 FRESH_DAYS 内一条；不回扫更早月份。
不是 24h 日榜（见 flash.py）。

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
import urllib.request
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_PATH = HERE / "output" / "json" / "chain_app_revenue_monthly.json"
STATE_PATH = HERE / "output" / "chain_app_revenue_monthly_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/chain-app-revenue"
API = "https://api.llama.fi/overview/fees"
UA = "dashboard-data/chain-app-revenue-monthly"
FRESH_DAYS = 14
TOP_N = 5
TZ_CN = timezone(timedelta(hours=8))

# 与日榜大致对齐的链样本
CHAINS: list[tuple[str, str]] = [
    ("solana", "Solana"),
    ("ethereum", "Ethereum"),
    ("hyperliquid", "Hyperliquid L1"),
    ("base", "Base"),
    ("polygon", "Polygon"),
    ("bsc", "BNB Chain"),
    ("arbitrum", "Arbitrum"),
    ("avalanche", "Avalanche"),
    ("tron", "Tron"),
    ("ton", "TON"),
    ("near", "Near"),
    ("bitcoin", "Bitcoin"),
    ("robinhood", "Robinhood Chain"),
]


def _ensure_lark_copy() -> None:
    folder = Path(__file__).resolve().parent
    while folder != folder.parent and not (folder / "lark_copy.py").is_file():
        folder = folder.parent
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))


_ensure_lark_copy()
from lark_copy import as_news  # noqa: E402


def env() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            out[key] = os.environ[key]
    return out


def send(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": as_news(text)}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(
            hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
        ).decode()
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode() or "{}")
    if str(body.get("code", "0")) != "0":
        raise RuntimeError(body)


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
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(min(2**attempt, 10))
    raise RuntimeError(f"GET {url}: {last}")


def last_closed_month(today: date) -> str:
    first = today.replace(day=1)
    prev = first - timedelta(days=1)
    return prev.strftime("%Y-%m")


def month_end(ym: str) -> date:
    y, m = int(ym[:4]), int(ym[5:7])
    return date(y, m, monthrange(y, m)[1])


def sum_month(chart: list[Any], ym: str) -> float:
    total = 0.0
    for pt in chart or []:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        if not isinstance(pt[1], (int, float)):
            continue
        day = datetime.fromtimestamp(int(pt[0]), tz=timezone.utc).strftime("%Y-%m")
        if day == ym:
            total += float(pt[1])
    return total


def yi(usd: float) -> str:
    if abs(usd) >= 1e8:
        return f"{usd / 1e8:.2f} 亿"
    return f"{usd / 1e4:.0f} 万"


def fetch_ranking(ym: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for slug, name in CHAINS:
        url = f"{API}/{slug}?dataType=dailyRevenue"
        try:
            doc = http_json(url)
        except Exception as exc:
            print(f"[warn] {slug}: {exc}")
            continue
        rev = sum_month(doc.get("totalDataChart") or [], ym)
        if rev <= 0:
            continue
        rows.append({"slug": slug, "name": name, "revenue_usd": rev})
        time.sleep(0.15)
    rows.sort(key=lambda r: r["revenue_usd"], reverse=True)
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return rows


def build_news(ym: str, ranking: list[dict[str, Any]]) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    top = ranking[:TOP_N]
    bits = []
    for i, row in enumerate(top):
        label = f"第{i + 1}" if i else "第一"
        bits.append(f"{row['name']}以约 {yi(row['revenue_usd'])}美元位居{label}")
    body = "，".join(bits) + "。"
    return (
        f"据吴说数据中心统计，{y} 年 {m} 月公链应用收入排名中，{body}"
        f"口径为 DefiLlama 按链 dailyRevenue 自然月加总，非 gas；与外电榜单可能略有差异。"
        f"\n{PAGE}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    args = ap.parse_args()

    today = datetime.now(timezone.utc).date()
    ym = last_closed_month(today)
    end = month_end(ym)
    age = (today - end).days
    if age > FRESH_DAYS:
        print(f"[skip] 最近闭合月 {ym} 已过期 age={age}d（>{FRESH_DAYS}）")
        return 0

    print(f"[chain-app-monthly] fetch {ym} …")
    ranking = fetch_ranking(ym)
    if len(ranking) < 3:
        print("[ERROR] 排名样本不足", file=sys.stderr)
        return 1

    doc = {
        "code": 0,
        "msg": "success",
        "data": {
            "month": ym,
            "ranking": ranking,
            "source": "DefiLlama overview/fees/{chain} dailyRevenue",
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print("top:", [(r["name"], round(r["revenue_usd"] / 1e6, 1)) for r in ranking[:5]])

    msg_id = f"chain-app-revenue-month-{ym}"
    state = load_state()
    if msg_id in state["sentIds"]:
        print(f"[skip] 已发送 {msg_id}")
        return 0

    text = build_news(ym, ranking)
    print(msg_id)
    print(text)
    if not args.push:
        print("[dry-run] 未推送")
        return 0
    conf = env()
    webhook = (conf.get("LARK_ALERT_WEBHOOK") or "").strip()
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 1
    send(webhook, (conf.get("LARK_ALERT_SECRET") or "").strip() or None, text)
    state["sentIds"] = list(set(state["sentIds"]) | {msg_id})
    save_state(state)
    print(f"[pushed] {msg_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
