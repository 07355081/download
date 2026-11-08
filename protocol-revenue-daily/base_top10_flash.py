#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Base 日收入重新跻身全市场 Top10 → Lark 新闻体。

数据：DefiLlama overview/fees dataType=dailyRevenue（免费，非 Token Terminal）。
只在 Base（slug=base）排名从 >10 变为 ≤10 时发最新一条；已在榜内不连推；
跌出后再进入可再发。不回扫历史。

用法：
  python base_top10_flash.py
  python base_top10_flash.py --push
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_PATH = HERE / "output" / "json" / "base_daily_revenue_rank.json"
STATE_PATH = HERE / "output" / "base_top10_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/protocol-revenue-daily"
API = (
    "https://api.llama.fi/overview/fees"
    "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true"
    "&dataType=dailyRevenue"
)
UA = "dashboard-data/base-top10-flash"
TARGET_SLUG = "base"
TOP_N = 10
TZ_CN = timezone(timedelta(hours=8))


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
        return {"sentIds": [], "last_rank": None}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": [], "last_rank": None}
    if not isinstance(doc, dict):
        return {"sentIds": [], "last_rank": None}
    ids = doc.get("sentIds")
    return {
        "sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else [],
        "last_rank": doc.get("last_rank"),
    }


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def fetch_ranking() -> list[dict[str, Any]]:
    req = urllib.request.Request(API, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as resp:
        doc = json.loads(resp.read().decode("utf-8"))
    rows: list[dict[str, Any]] = []
    for p in doc.get("protocols") or []:
        if not isinstance(p, dict):
            continue
        v = p.get("total24h")
        if v is None:
            continue
        slug = str(p.get("slug") or "").strip()
        name = str(p.get("name") or p.get("displayName") or slug)
        cat = str(p.get("category") or "")
        rows.append({"slug": slug, "name": name, "category": cat, "revenue_usd": float(v)})
    rows.sort(key=lambda r: r["revenue_usd"], reverse=True)
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return rows


def usd_text(v: float) -> str:
    if v >= 100_000_000:
        return f"{v / 100_000_000:.2f}".rstrip("0").rstrip(".") + " 亿"
    if v >= 10_000:
        return f"{v / 10_000:.1f}".rstrip("0").rstrip(".") + " 万"
    return f"{v:,.0f}"


def cat_summary(top: list[dict[str, Any]]) -> str:
    cats = []
    for row in top:
        c = (row.get("category") or "").strip()
        if c and c not in cats:
            cats.append(c)
    if not cats:
        return "稳定币发行商、公链、交易应用等"
    # 中文粗映射
    mapping = {
        "Stablecoin Issuer": "稳定币发行商",
        "Chain": "公链",
        "Dexs": "交易应用",
        "Derivatives": "衍生品",
        "Launchpad": "发射平台",
        "Lending": "借贷",
        "Trading App": "交易应用",
        "Wallet": "钱包",
        "CDP": "稳定币/CDP",
        "RWA": "RWA",
        "Prediction Market": "预测市场",
    }
    zh = [mapping.get(c, c) for c in cats[:6]]
    return "、".join(zh)


def build_news(asof: str, rank: int, revenue: float, top: list[dict[str, Any]]) -> str:
    return (
        f"据吴说数据中心统计，截至 {asof}（UTC），Base 网络日收入约 {usd_text(revenue)}美元，"
        f"位列 DefiLlama 全市场协议日收入排行榜第 {rank} 名，重新跻身前 {TOP_N}。"
        f"当日 Top {TOP_N} 主要由{cat_summary(top)}等占据。"
        f"口径为 DefiLlama dailyRevenue，非 Token Terminal。"
        f"\n{PAGE}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    args = ap.parse_args()

    ranking = fetch_ranking()
    base = next((r for r in ranking if r["slug"] == TARGET_SLUG), None)
    if base is None:
        print("[skip] 未找到 Base")
        return 0
    rank = int(base["rank"])
    revenue = float(base["revenue_usd"])
    asof = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    top = ranking[:TOP_N]

    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "asof": asof,
        "base": base,
        "top": top,
        "source": "DefiLlama dailyRevenue",
        "fetched_at": fetched_at,
    }
    doc = {
        "code": 0,
        "msg": "success",
        "name": "base_daily_revenue_rank",
        "asof": asof,
        "day": asof,
        "base": base,
        "top": top,
        "source": "DefiLlama dailyRevenue",
        "fetched_at": fetched_at,
        "data": payload,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)

    state = load_state()
    prev = state.get("last_rank")
    prev_i = int(prev) if isinstance(prev, (int, float)) else None
    entered = rank <= TOP_N and (prev_i is None or prev_i > TOP_N)
    # 首次建基线且已在榜外：只记 rank 不发
    if prev_i is None and rank > TOP_N:
        state["last_rank"] = rank
        save_state(state)
        print(f"[baseline] Base rank={rank} revenue={revenue:.0f}（未进 Top{TOP_N}，不推）")
        return 0
    # 首次建基线且已在榜内：视为重新观测到，可发一次
    if prev_i is None and rank <= TOP_N:
        entered = True

    msg_id = f"base-rev-top10-{asof}"
    if not entered:
        state["last_rank"] = rank
        save_state(state)
        print(f"[skip] Base rank={rank} prev={prev_i}（未发生跻身 Top{TOP_N}）")
        return 0
    if msg_id in state["sentIds"]:
        state["last_rank"] = rank
        save_state(state)
        print(f"[skip] 已发送 {msg_id}")
        return 0

    text = build_news(asof, rank, revenue, top)
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
    state["last_rank"] = rank
    save_state(state)
    print(f"[pushed] {msg_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
