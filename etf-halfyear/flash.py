#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""美现货 BTC/ETH ETF 半年度净流 → Lark 新闻体。

只发最新一个已收完半年度；结束后超过 90 天不发；不回扫更早半年度。
正文含 BTC/ETH 半年度净流、是否首次半年度净流出、该半最长连续流出、IBIT 半年度净流。
非 DWF Labs。

用法：
  python flash.py
  python flash.py --push
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    HERE / "output" / "json" / "etf_halfyear.json",
    Path("/root/dashboard/public/json/etf-halfyear/etf_halfyear.json"),
]
STATE_PATH = HERE / "output" / "etf_halfyear_lark.json"
LOG_PATH = HERE / "flash.log"
PAGE = "https://data.wublock123.com/dashboard/etf"
JSON_URL = "https://data.wublock123.com/dashboard/etf"
FRESH_DAYS = 90
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
            if isinstance(doc, dict) and doc.get("latest_complete_id"):
                print(f"[etf-halfyear] json={path}")
                return doc
    raise FileNotFoundError("找不到 etf_halfyear.json")


def yi(usd: float) -> str:
    sign = "" if usd >= 0 else "−"
    return f"{sign}{abs(usd) / 1e8:.2f} 亿美元"


def dir_zh(usd: float) -> str:
    return "净流入" if usd >= 0 else "净流出"


def half_zh(half: int) -> str:
    return "上半年" if half == 1 else "下半年"


def pick(rows: list[dict[str, Any]], hid: str) -> dict[str, Any] | None:
    for row in rows:
        if str(row.get("id")) == hid:
            return row
    return None


def build_news(hid: str, btc: dict[str, Any] | None, eth: dict[str, Any] | None) -> str:
    year = int((btc or eth or {}).get("year") or hid[:4])
    half = int((btc or eth or {}).get("half") or (1 if hid.endswith("H1") else 2))
    bits: list[str] = []
    if btc and btc.get("flow_usd") is not None:
        flow = float(btc["flow_usd"])
        first = "，为有序列以来首次半年度净流出" if btc.get("first_halfyear_outflow") and flow < 0 else ""
        bits.append(
            f"美国现货比特币 ETF {year} 年{half_zh(half)}{dir_zh(flow)}约 {yi(flow)}{first}"
        )
        streak = btc.get("outflow_streak") or {}
        if streak.get("days"):
            ath = "，创本通道已记录最长连续流出" if btc.get("streak_is_series_max") else ""
            bits.append(
                f"该半内最长连续净流出 {int(streak['days'])} 个交易日"
                f"（{streak.get('start')} 至 {streak.get('end')}）{ath}"
            )
        ibit = btc.get("ibit_flow_usd")
        if isinstance(ibit, (int, float)):
            bits.append(f"其中 BlackRock IBIT 同期{dir_zh(float(ibit))}约 {yi(float(ibit))}")
    if eth and eth.get("flow_usd") is not None:
        flow = float(eth["flow_usd"])
        first = "，亦为有序列以来首次半年度净流出" if eth.get("first_halfyear_outflow") and flow < 0 else ""
        bits.append(
            f"美国现货以太坊 ETF 同期{dir_zh(flow)}约 {yi(flow)}{first}"
        )
    body = "；".join(bits) if bits else "半年度净流已更新"
    return (
        f"吴说获悉，据吴说数据中心统计，{body}。"
        f"口径为 CoinGlass 美现货 ETF 日净流按半年度加总，非 DWF Labs；"
        f"数字可能与原文略有差异。"
        f"\n{PAGE}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--allow-stale", action="store_true")
    args = parser.parse_args()
    try:
        doc = load_doc()
    except (OSError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    hid = str(doc.get("latest_complete_id") or "")
    btc = pick(doc.get("btc") or [], hid)
    eth = pick(doc.get("eth") or [], hid)
    if not hid or (not btc and not eth):
        print("[skip] 无已收完半年度")
        return 0

    end = date.fromisoformat(str((btc or eth)["end"]))
    age = (datetime.now(TZ_CN).date() - end).days
    print(
        f"[etf-halfyear] id={hid} age={age}d "
        f"btc={None if not btc else btc.get('flow_usd')} "
        f"eth={None if not eth else eth.get('flow_usd')}"
    )
    if age > FRESH_DAYS and not args.allow_stale and not args.force:
        print(f"[skip] 半年度已结束 {age} 天，超过 {FRESH_DAYS} 天，不补历史")
        return 0

    msg_id = f"etf-halfyear-{hid}"
    text = as_news(build_news(hid, btc, eth))
    print("---")
    print(f"id={msg_id}")
    print(text)

    state = load_state()
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
        return 0
    if not args.push:
        print("\n[dry-run] 未推送")
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
    state["sentIds"] = (state["sentIds"] + [msg_id])[-40:]
    save_state(state)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} {msg_id} ok=True\n{text}\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
