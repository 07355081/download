#!/usr/bin/env python3
"""加密卡按卡商月成交 → Lark 新闻体（Paymentscan 公开页）。

只发最近已闭合自然月，且月末后 ≤14 天：
  - 合计月成交创本通道新高，或
  - RedotPay 月成交创本通道新高
首月/窗口外建基线不推；不回扫历史月（含外电 4 月）。

用法：
  python flash_programs.py
  python flash_programs.py --push
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    HERE / "output" / "json" / "crypto_card_programs.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "crypto-card-programs" / "crypto_card_programs.json",
    Path("/root/dashboard/public/json/crypto-card-programs/crypto_card_programs.json"),
]
STATE_PATH = HERE / "output" / "crypto_card_programs_lark.json"
LOG_PATH = HERE / "flash_programs.log"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/crypto-card-programs"
TZ_CN = timezone(timedelta(hours=8))
WINDOW_DAYS = 14
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
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
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


def load_json() -> dict[str, Any]:
    for path in JSON_CANDIDATES:
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("months"), list):
                print(f"[crypto-card-programs] json={path}")
                return doc
    raise FileNotFoundError("找不到 crypto_card_programs.json，请先跑 download_programs.py")


def month_end(ym: str) -> datetime:
    y, m = int(ym[:4]), int(ym[5:7])
    last = monthrange(y, m)[1]
    return datetime(y, m, last, tzinfo=timezone.utc)


def last_closed_month(now: datetime) -> str:
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev = first - timedelta(days=1)
    return prev.strftime("%Y-%m")


def yi(usd: float) -> str:
    if abs(usd) >= 1e8:
        return f"{usd / 1e8:.2f} 亿美元"
    return f"{usd / 1e4:.0f} 万美元"


def build_news(row: dict[str, Any], total_ath: bool, rp_ath: bool) -> str:
    ym = str(row["month"])
    y, m = int(ym[:4]), int(ym[5:7])
    total = float(row["total_usd"])
    rp = float(row["redotpay_usd"])
    share = row.get("redotpay_share_pct")
    cum = float(row.get("redotpay_cum_usd") or 0)
    cum_share = row.get("redotpay_cum_share_pct")
    bits: list[str] = []
    if total_ath:
        bits.append("创本通道合计月成交新高")
    if rp_ath:
        bits.append("创本通道 RedotPay 月成交新高")
    flag = "、".join(bits) if bits else "月度更新"
    share_bit = f"（占当月约 {share:.1f}%）" if isinstance(share, (int, float)) else ""
    cum_bit = ""
    if cum > 0:
        cum_share_s = f"，约占样本累计约 {cum_share:.1f}%" if isinstance(cum_share, (int, float)) else ""
        cum_bit = f"RedotPay 累计成交约 {yi(cum)}{cum_share_s}。"
    return (
        f"据吴说数据中心统计，{y} 年 {m} 月加密支付卡（Paymentscan 卡商口径）合计成交约 {yi(total)}，"
        f"{flag}。其中 RedotPay 约 {yi(rp)}{share_bit}。{cum_bit}"
        f"口径为 Paymentscan 公开首页 spends 近似，含部分自报/链下数据，非 Dune 面板；数字可能与外电略有差异。"
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

    months = [row for row in doc.get("months") or [] if isinstance(row, dict) and row.get("month")]
    if not months:
        print("[ERROR] 无月份数据", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    closed = last_closed_month(now)
    by_month = {str(row["month"]): row for row in months}
    if closed not in by_month:
        print(f"[skip] 最近闭合月 {closed} 尚无数据")
        return 0

    row = by_month[closed]
    end = month_end(closed)
    age_days = (now.date() - end.date()).days
    if age_days > WINDOW_DAYS and not args.force:
        print(f"[skip] {closed} 已结束 {age_days} 天，超过 {WINDOW_DAYS} 天窗口（建基线不推历史）")
        return 0

    prior = [by_month[ym] for ym in sorted(by_month) if ym < closed]
    total = float(row["total_usd"])
    rp = float(row["redotpay_usd"])
    prior_total_high = max((float(r["total_usd"]) for r in prior), default=None)
    prior_rp_high = max((float(r["redotpay_usd"]) for r in prior), default=None)
    total_ath = prior_total_high is not None and total > prior_total_high + 1.0
    rp_ath = prior_rp_high is not None and rp > prior_rp_high + 1.0

    print(
        f"[crypto-card-programs] {closed} total={total/1e8:.2f}亿 rp={rp/1e8:.2f}亿 "
        f"total_ath={total_ath} rp_ath={rp_ath} age={age_days}d"
    )

    if not prior:
        print("[skip] 首月建基线，不推")
        return 0
    if not total_ath and not rp_ath and not args.force:
        print("[skip] 合计与 RedotPay 均未创月新高")
        return 0

    msg_id = f"crypto-card-programs-{closed}"
    if total_ath and rp_ath:
        msg_id += "-both-ath"
    elif total_ath:
        msg_id += "-total-ath"
    else:
        msg_id += "-redot-ath"

    text = build_news(row, total_ath, rp_ath)
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
        return 3
    state["sentIds"] = (state.get("sentIds") or []) + [msg_id]
    save_state(state)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"{stamp}\t{msg_id}\tok\n")
    print(f"[ok] pushed {msg_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
