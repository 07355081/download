#!/usr/bin/env python3
"""RLUSD 以太坊 / XRPL 流通占比 → Lark 新闻体。

只发最新完整日（距今 ≤3 天）。不回扫、不发 Q1 转账。
- 日常：总量、ETH/XRPL 金额与占比
- 阈值：ETH 占比刚越过 50%，或日占比变动 |Δ|≥2 个百分点，或总量日环比 |%|≥5

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    HERE / "output" / "json" / "rlusd_chain.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "rlusd-chain" / "rlusd_chain.json",
    Path("/root/dashboard/public/json/rlusd-chain/rlusd_chain.json"),
]
STATE_PATH = HERE / "output" / "rlusd_chain_lark.json"
LOG_PATH = HERE / "flash.log"
JSON_URL = "https://data.wublock123.com/dashboard/stablecoin-marketcap"
PAGE = "https://data.wublock123.com/dashboard/stablecoin-marketcap"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
MARK_DAILY = "【实时日常报告】"
MARK_THRESHOLD = "【历史阈值警报】"
FRESH_DAYS = 3
SHARE_LINE = 50.0
SHARE_DOD_PP = 2.0
TOTAL_DOD_PCT = 5.0

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
    body = as_news(text)
    for mark in (MARK_THRESHOLD, MARK_DAILY):
        if text.startswith(mark):
            body = f"{mark}\n{body}"
            break
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": body}}
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
        raw = resp.read().decode("utf-8", errors="replace")
    if raw:
        result = json.loads(raw)
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


def append_log(stamp: str, msg_id: str, ok: bool, text: str) -> None:
    line = json.dumps({"ts": stamp, "id": msg_id, "ok": ok, "text": text}, ensure_ascii=False)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def load_rows() -> list[dict[str, Any]]:
    for path in JSON_CANDIDATES:
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        rows = doc.get("data") if isinstance(doc, dict) else None
        if isinstance(rows, list):
            out = [r for r in rows if isinstance(r, dict) and r.get("day")]
            out.sort(key=lambda r: str(r["day"]))
            print(f"[rlusd-chain] json={path} n={len(out)}")
            return out
    raise FileNotFoundError("找不到 rlusd_chain.json，请先跑 download.py")


def yi(usd: float) -> str:
    a = abs(usd) / 1e8
    if a >= 10:
        return f"{a:.1f} 亿美元"
    return f"{a:.2f} 亿美元"


def cn_day(day: str) -> str:
    y, m, d = day.split("-")
    return f"{int(y)} 年 {int(m)} 月 {int(d)} 日"


def fresh(day: str) -> bool:
    d = datetime.strptime(day, "%Y-%m-%d").date()
    return (datetime.now(timezone.utc).date() - d).days <= FRESH_DAYS


def daily_text(row: dict[str, Any]) -> str:
    return (
        f"据吴说数据中心统计，截至 {cn_day(str(row['day']))}，"
        f"RLUSD 在以太坊与 XRPL 合计流通约 {yi(float(row['total_usd']))}，"
        f"其中以太坊约 {yi(float(row['eth_usd']))}、占比约 {float(row['eth_share_pct']):.1f}%；"
        f"XRPL 约 {yi(float(row['xrpl_usd']))}、占比约 {float(row['xrpl_share_pct']):.1f}%。"
        f"口径为 DefiLlama 流通量，仅统计这两条链，不含转账额。"
        f"\n{PAGE}"
    )


def threshold_hit(row: dict[str, Any], prev: dict[str, Any] | None) -> list[str]:
    reasons: list[str] = []
    eth = float(row["eth_share_pct"])
    if prev is not None:
        prev_eth = float(prev["eth_share_pct"])
        if (prev_eth < SHARE_LINE <= eth) or (prev_eth > SHARE_LINE >= eth):
            reasons.append(f"以太坊占比约 {eth:.1f}%（刚越过 {SHARE_LINE:.0f}%）")
        dod = row.get("eth_share_dod_pp")
        if isinstance(dod, (int, float)) and abs(float(dod)) >= SHARE_DOD_PP:
            sign = "+" if float(dod) > 0 else ""
            reasons.append(f"以太坊占比日变 {sign}{float(dod):.1f} 个百分点")
        td = row.get("total_dod_pct")
        if isinstance(td, (int, float)) and abs(float(td)) >= TOTAL_DOD_PCT:
            sign = "+" if float(td) > 0 else ""
            reasons.append(f"两链合计流通日环比约 {sign}{float(td):.1f}%")
    return reasons


def threshold_text(row: dict[str, Any], reasons: list[str]) -> str:
    return (
        f"据吴说数据中心统计，截至 {cn_day(str(row['day']))}，RLUSD 分链流通出现明显变化："
        f"{'；'.join(reasons)}。"
        f"两链合计约 {yi(float(row['total_usd']))}，"
        f"以太坊约 {yi(float(row['eth_usd']))}（{float(row['eth_share_pct']):.1f}%），"
        f"XRPL 约 {yi(float(row['xrpl_usd']))}（{float(row['xrpl_share_pct']):.1f}%）。"
        f"口径为 DefiLlama 流通量，不含转账额。"
        f"\n{PAGE}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-daily", action="store_true")
    args = parser.parse_args()
    try:
        rows = load_rows()
    except (OSError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    if not rows:
        print("[rlusd-chain] 无数据")
        return 0
    latest = rows[-1]
    day = str(latest["day"])
    if not fresh(day):
        print(f"[skip] 最新日 {day} 超过 {FRESH_DAYS} 天，不发")
        return 0
    prev = rows[-2] if len(rows) >= 2 else None
    briefs: list[dict[str, str]] = []
    if not args.no_daily:
        briefs.append({"id": f"rlusd-chain-daily-{day}", "text": f"{MARK_DAILY}\n{daily_text(latest)}"})
    reasons = threshold_hit(latest, prev)
    if reasons:
        briefs.append(
            {
                "id": f"rlusd-chain-threshold-{day}",
                "text": f"{MARK_THRESHOLD}\n{threshold_text(latest, reasons)}",
            }
        )
    else:
        print("[info] 未过阈值线")

    state = load_state()
    for brief in briefs:
        print("---")
        print(f"id={brief['id']} already={brief['id'] in state['sentIds']}")
        print(as_news(brief["text"]))
    if not briefs:
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
    sent_any = False
    for brief in briefs:
        if brief["id"] in state["sentIds"] and not args.force:
            print(f"[skip] already {brief['id']}")
            continue
        try:
            send_lark(webhook, secret, brief["text"])
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] Lark 失败 {brief['id']}: {exc}", file=sys.stderr)
            append_log(stamp, brief["id"], False, brief["text"])
            return 1
        print(f"[pushed] {brief['id']}")
        if brief["id"] not in state["sentIds"]:
            state["sentIds"].append(brief["id"])
        sent_any = True
        append_log(stamp, brief["id"], True, brief["text"])
        time.sleep(0.4)
    if sent_any:
        state["sentIds"] = state["sentIds"][-200:]
        save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
