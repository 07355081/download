#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Solana vs Ethereum 月度 DEX 现货成交占比 → Lark 新闻体。

数据：已有 dex_volume.json（DefiLlama dailyVolume），非 The Block。
只发最新完整自然月；月末超过 45 天不发；不回扫。
触发：Solana/Ethereum 月成交占比创近 12 个完整月新低。
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
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    HERE / "output" / "json" / "dex_volume.json",
    Path("/root/dashboard/public/json/dex-volume/dex_volume.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "dex-volume" / "dex_volume.json",
]
STATE_PATH = HERE / "output" / "sol_eth_ratio_lark.json"
LOG_PATH = HERE / "sol_eth_ratio_flash.log"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/dex-volume"
FRESH_DAYS = 45
LOOKBACK = 12
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


def resolve_json() -> Path:
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 dex_volume.json")


def complete_months(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sol_m: dict[str, float] = defaultdict(float)
    eth_m: dict[str, float] = defaultdict(float)
    days: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        day = str(row.get("time") or "")
        if len(day) < 10:
            continue
        ym = day[:7]
        days[ym].add(day)
        if isinstance(row.get("solana"), (int, float)):
            sol_m[ym] += float(row["solana"])
        if isinstance(row.get("ethereum"), (int, float)):
            eth_m[ym] += float(row["ethereum"])
    out = []
    for ym in sorted(set(sol_m) & set(eth_m)):
        y, m = int(ym[:4]), int(ym[5:7])
        if len(days[ym]) < monthrange(y, m)[1]:
            continue
        sol, eth = sol_m[ym], eth_m[ym]
        if eth <= 0:
            continue
        out.append(
            {
                "month": ym,
                "solana_usd": sol,
                "ethereum_usd": eth,
                "ratio_pct": sol / eth * 100.0,
            }
        )
    return out


def month_end(ym: str) -> date:
    y, m = int(ym[:4]), int(ym[5:7])
    return date(y, m, monthrange(y, m)[1])


def fmt_b(n: float) -> str:
    return f"{n / 1e9:.0f} 亿美元" if n >= 10e9 else f"{n / 1e9:.1f} 亿美元"


def build_news(cur: dict[str, Any], peak: dict[str, Any] | None) -> str:
    ym = cur["month"]
    y, m = int(ym[:4]), int(ym[5:7])
    bits = [
        f"据吴说数据中心统计，{y} 年 {m} 月 Solana DEX 现货成交约 {fmt_b(cur['solana_usd'])}，"
        f"Ethereum 约 {fmt_b(cur['ethereum_usd'])}，"
        f"Solana 相对 Ethereum 的成交占比约 {cur['ratio_pct']:.0f}%，"
        f"创近 {LOOKBACK} 个完整月新低"
    ]
    if peak and peak["month"] != ym:
        py, pm = int(peak["month"][:4]), int(peak["month"][5:7])
        bits.append(
            f"。近窗内高点为 {py} 年 {pm} 月约 {peak['ratio_pct']:.0f}%"
        )
    return (
        "".join(bits)
        + "。口径为 DefiLlama DEX 现货日成交加总，非 The Block。"
        f"\n{PAGE}"
    )


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


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": as_news(text)}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(
            hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
        ).decode("utf-8")
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        doc = json.loads(resolve_json().read_text(encoding="utf-8"))
        rows = doc.get("data") if isinstance(doc, dict) else None
        if not isinstance(rows, list) or not rows:
            raise RuntimeError("dex_volume 无日序列")
        months = complete_months(rows)
    except (OSError, RuntimeError, json.JSONDecodeError, FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    if len(months) < LOOKBACK + 1:
        print(f"[skip] 完整月不足 {LOOKBACK}+1（现 {len(months)}）")
        return 0

    latest = months[-1]
    window = months[-(LOOKBACK + 1) :]
    prior = window[:-1]
    prior_min = min(prior, key=lambda x: x["ratio_pct"])
    peak = max(window, key=lambda x: x["ratio_pct"])
    is_low = latest["ratio_pct"] < prior_min["ratio_pct"] - 1e-9
    age = (date.today() - month_end(latest["month"])).days
    print(
        f"[sol-eth-ratio] month={latest['month']} sol={latest['solana_usd']/1e9:.1f}B "
        f"eth={latest['ethereum_usd']/1e9:.1f}B ratio={latest['ratio_pct']:.1f}% "
        f"prior_min={prior_min['month']} {prior_min['ratio_pct']:.1f}% "
        f"new_low={is_low} age={age}d"
    )
    if age > FRESH_DAYS and not args.force:
        print(f"[skip] 月末已过 {age} 天")
        return 0
    if not is_low and not args.force:
        print("[skip] 未创近12月占比新低")
        return 0

    msg_id = f"dex-sol-eth-ratio-low-{latest['month']}"
    text = as_news(build_news(latest, peak))
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
    state["sentIds"] = state["sentIds"][-100:]
    save_state(state)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} {msg_id} ok=True\n{text}\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
