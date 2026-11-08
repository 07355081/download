#!/usr/bin/env python3
"""Aave v3 Ethereum 池内 WETH 市值 → Lark 新闻体。

只发最新完整日（≤3 天）：
- 日常：池内 WETH 市值
- 阈值：创序列新高，或 |日环比|≥10%

口径声明：DefiLlama 池内折美元，非 available liquidity，不写「全 DeFi 最深」。
不回扫 4 月危机历史窗。

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
    HERE / "output" / "json" / "aave_v3_weth.json",
    Path("/root/dashboard/public/json/aave-v3-weth/aave_v3_weth.json"),
]
STATE_PATH = HERE / "output" / "aave_v3_weth_lark.json"
LOG_PATH = HERE / "flash.log"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/aave-v3-weth"
FRESH_DAYS = 3
DOD_PCT = 10.0
MARK_DAILY = "【实时日常报告】"
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
    body = as_news(text)
    if text.startswith(MARK_DAILY):
        body = f"{MARK_DAILY}\n{body}"
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


def load_json() -> dict[str, Any]:
    for path in JSON_CANDIDATES:
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("days"), list):
                print(f"[aave-v3-weth] json={path}")
                return doc
    raise FileNotFoundError("找不到 aave_v3_weth.json")


def yi(usd: float) -> str:
    if abs(usd) >= 1e8:
        return f"{usd / 1e8:.2f} 亿美元"
    return f"{usd / 1e4:.0f} 万美元"


def daily_text(row: dict[str, Any]) -> str:
    day = str(row["day"])
    usd = float(row["weth_usd"])
    dod = row.get("dod_pct")
    dod_bit = f"，较前一日约 {dod:+.1f}%" if isinstance(dod, (int, float)) else ""
    return (
        f"据吴说数据中心统计，截至 {day}（UTC），Aave v3 以太坊主网池内 WETH 折合约 {yi(usd)}"
        f"{dod_bit}。口径为 DefiLlama tokensInUsd 近似，反映池内 WETH 市值，"
        f"不是 available liquidity，也不表示全 DeFi 最深 WETH 池；数字可能与外电略有差异。"
        f"\n{PAGE}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    try:
        doc = load_json()
    except (OSError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    days = [row for row in doc.get("days") or [] if isinstance(row, dict) and row.get("day")]
    if len(days) < 2:
        print("[ERROR] 日序列不足", file=sys.stderr)
        return 1

    latest = days[-1]
    day = str(latest["day"])
    try:
        day_dt = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        print(f"[ERROR] 坏日期 {day}", file=sys.stderr)
        return 1
    age = (datetime.now(timezone.utc).date() - day_dt.date()).days
    if age > FRESH_DAYS and not args.force:
        print(f"[skip] {day} 距今 {age} 天")
        return 0

    usd = float(latest["weth_usd"])
    prior = days[:-1]
    prior_high = max(float(r["weth_usd"]) for r in prior)
    is_ath = usd > prior_high + 1.0
    dod = latest.get("dod_pct")
    dod_hit = isinstance(dod, (int, float)) and abs(float(dod)) >= DOD_PCT
    reasons: list[str] = []
    if is_ath:
        reasons.append("池内 WETH 市值创本通道新高")
    if dod_hit:
        reasons.append(f"日环比约 {float(dod):+.1f}%")

    briefs = [{"id": f"aave-v3-weth-daily-{day}", "text": f"{MARK_DAILY}\n{daily_text(latest)}"}]
    if reasons:
        briefs.append(
            {
                "id": f"aave-v3-weth-threshold-{day}",
                "text": daily_text(latest) + f"触发：{'；'.join(reasons)}。",
            }
        )

    print(f"[aave-v3-weth] {day} weth={usd/1e8:.2f}亿 ath={is_ath} dod={dod} reasons={reasons}")
    for brief in briefs:
        print("---")
        print(brief["id"])
        print(brief["text"] if brief["text"].startswith(MARK_DAILY) else as_news(brief["text"]))

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
    sent = set(state.get("sentIds") or [])
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    for brief in briefs:
        msg_id = brief["id"]
        if msg_id in sent and not args.force:
            print(f"[skip] already {msg_id}")
            continue
        try:
            send_lark(webhook, secret, brief["text"])
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] Lark 失败 {msg_id}: {exc}", file=sys.stderr)
            return 3
        sent.add(msg_id)
        print(f"[ok] pushed {msg_id}")
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp}\t{msg_id}\tok\n")
    state["sentIds"] = sorted(sent)
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
