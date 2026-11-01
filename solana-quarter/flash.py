#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Solana 最近一个完整季度 → Lark 新闻。

只发季末之后 45 天内的最新一季。不回扫更早季度。
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
JSON_CANDIDATES = [
    HERE / "output" / "json" / "solana_quarter.json",
    Path("/root/dashboard/public/json/solana-quarter/solana_quarter.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "solana-quarter" / "solana_quarter.json",
]
STATE_PATH = HERE / "output" / "solana_quarter_lark.json"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/solana-quarter"
FRESH_DAYS = 45
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


def yi(usd: float) -> str:
    return f"{usd / 1e8:.2f} 亿美元"


def pct(change: float) -> str:
    verb = "增长" if change >= 0 else "下降"
    return f"{verb} {round(abs(change) * 100)}%"


def quarter_label(qid: str) -> str:
    year, quarter = qid.split("Q")
    names = {"1": "第一", "2": "第二", "3": "第三", "4": "第四"}
    return f"{year} 年{names.get(quarter, quarter)}季度"


def prev_id(qid: str) -> str:
    year, quarter = qid.split("Q")
    q = int(quarter) - 1
    y = int(year)
    if q == 0:
        y -= 1
        q = 4
    return f"{y}Q{q}"


def build_text(latest: dict[str, Any], prev: dict[str, Any] | None) -> str:
    parts = [f"据吴说数据中心统计，{quarter_label(latest['id'])} Solana 链上应用收入合计约 {yi(latest['app_revenue_usd'])}"]
    if prev and prev.get("app_revenue_usd"):
        change = latest["app_revenue_usd"] / prev["app_revenue_usd"] - 1
        parts.append(f"，环比上季{pct(change)}")
    parts.append("。")
    pump = float(latest.get("pump_revenue_usd") or 0)
    if pump > 0:
        share = latest.get("pump_share")
        share_s = f"，约占该季应用收入的 {round(float(share) * 100)}%" if share else ""
        lead = "收入最高的应用仍是 Pump.fun，约 " if latest.get("pump_is_top") else "Pump.fun 收入约 "
        parts.append(f"{lead}{yi(pump)}{share_s}。")
    tvl = latest.get("tvl_end_usd")
    if isinstance(tvl, (int, float)) and tvl > 0:
        sentence = f"季末 DeFi TVL 约 {yi(float(tvl))}"
        prev_tvl = prev.get("tvl_end_usd") if prev else None
        if isinstance(prev_tvl, (int, float)) and prev_tvl > 0:
            sentence += f"，较上季末{pct(float(tvl) / float(prev_tvl) - 1)}"
        parts.append(sentence + "。")
    onre = latest.get("onre_end_usd")
    if isinstance(onre, (int, float)) and onre > 0:
        sentence = f"OnRe 季末 TVL 约 {yi(float(onre))}"
        prev_onre = prev.get("onre_end_usd") if prev else None
        if isinstance(prev_onre, (int, float)) and prev_onre > 0:
            sentence += f"，较上季末{pct(float(onre) / float(prev_onre) - 1)}"
        parts.append(sentence + "。")
    parts.append("口径为 DefiLlama，不是 Messari 的 REV，也不含整条 Solana RWA。")
    return "".join(parts) + f"\n{PAGE}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    path = next((item for item in JSON_CANDIDATES if item.is_file()), None)
    if path is None:
        raise FileNotFoundError("找不到 solana_quarter.json")
    doc = json.loads(path.read_text(encoding="utf-8"))
    quarters = [row for row in doc.get("quarters") or [] if isinstance(row, dict) and row.get("id")]
    if not quarters:
        print("[solana-quarter] skip empty")
        return 0
    latest = max(quarters, key=lambda row: str(row.get("end") or ""))
    end = datetime.strptime(str(latest["end"]), "%Y-%m-%d").date()
    age = (datetime.now(timezone.utc).date() - end).days
    print(f"[solana-quarter] latest={latest['id']} end={latest['end']} age={age}")
    if age < 0 or age > FRESH_DAYS:
        print("[solana-quarter] skip outside freshness window")
        return 0
    prev = next((row for row in quarters if row.get("id") == prev_id(str(latest["id"]))), None)
    text = build_text(latest, prev)
    msg_id = f"solana-quarter-{latest['id']}"
    state = load_state()
    if msg_id in state["sentIds"] and not args.force:
        print(f"[solana-quarter] already {msg_id}")
        return 0
    if not args.push:
        print(text)
        return 0
    env = load_env()
    webhook = env.get("LARK_ALERT_WEBHOOK") or ""
    if not webhook:
        raise RuntimeError("缺少 LARK_ALERT_WEBHOOK")
    send_lark(webhook, env.get("LARK_ALERT_SECRET"), text)
    state["sentIds"] = [*state["sentIds"], msg_id][-12:]
    save_state(state)
    print(f"[solana-quarter] Lark OK {msg_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[solana-quarter] ERROR {exc}", file=sys.stderr)
        raise SystemExit(1)
