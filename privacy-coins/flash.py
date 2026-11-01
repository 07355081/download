# -*- coding: utf-8 -*-
"""隐私币板块（CoinGecko）→ Lark 新闻体，只推最新一条。

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
    HERE / "output" / "json" / "privacy_coins.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "privacy-coins" / "privacy_coins.json",
    Path("/root/dashboard/public/json/privacy-coins/privacy_coins.json"),
]
STATE_PATH = HERE / "output" / "privacy_coins_lark.json"
LOG_PATH = HERE / "flash.log"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/lark-feeds/privacy-coins"

ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))

sys.path.insert(0, str(ROOT))
from lark_copy import as_news  # noqa: E402


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def resolve_json(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 privacy_coins.json")


def fmt_yi(usd: float | None) -> str:
    if usd is None:
        return "—"
    yi = float(usd) / 1e8
    if abs(yi) >= 100:
        return f"{yi:.0f} 亿美元"
    if abs(yi) >= 10:
        return f"{yi:.1f} 亿美元"
    return f"{yi:.2f} 亿美元"


def fmt_pct(pct: float | None) -> str:
    if pct is None:
        return "—"
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def build_news(doc: dict[str, Any]) -> tuple[str, str]:
    """Return (msg_id, news_text)."""
    cat = doc.get("category") or {}
    btc = doc.get("btc") or {}
    zec = doc.get("zec") or {}
    sector = doc.get("sector") or {}
    as_of = str(doc.get("as_of") or "")[:10] or datetime.now(TZ_CN).strftime("%Y-%m-%d")
    day = as_of[:10]
    msg_id = f"privacy-coins-daily-{day}"

    mcap = cat.get("market_cap_usd")
    chg24 = cat.get("market_cap_change_24h_pct")
    w30 = sector.get("weighted_30d_pct")
    w1y = sector.get("weighted_1y_pct")
    ex1y = sector.get("ex_zec_weighted_1y_pct")
    btc_ath = btc.get("ath_change_pct")
    zec_share = zec.get("share_pct")
    zec_rank = zec.get("market_cap_rank")
    zec_1y = zec.get("price_change_1y_pct")
    zec_mcap = zec.get("market_cap_usd")

    y, m, d = day.split("-")
    body = (
        f"据吴说数据中心统计，截至 {int(y)} 年 {int(m)} 月 {int(d)} 日，"
        f"CoinGecko「Privacy Coins」板块流通市值约 {fmt_yi(float(mcap) if mcap is not None else None)}"
        f"（24 小时约 {fmt_pct(float(chg24) if chg24 is not None else None)}）；"
        f"市值加权近 30 日约 {fmt_pct(float(w30) if w30 is not None else None)}，"
        f"近一年约 {fmt_pct(float(w1y) if w1y is not None else None)}。"
    )
    if zec:
        body += (
            f"ZEC 市值约 {fmt_yi(float(zec_mcap) if zec_mcap is not None else None)}"
            f"（全市场排名约第 {zec_rank}），约占该板块 "
            f"{(f'{float(zec_share):.1f}%' if zec_share is not None else '—')}；"
            f"近一年涨跌约 {fmt_pct(float(zec_1y) if zec_1y is not None else None)}。"
        )
    if ex1y is not None:
        body += f"剔除 ZEC 后，板块市值加权近一年约 {fmt_pct(float(ex1y))}。"
    if btc_ath is not None:
        body += (
            f"同期比特币较历史最高点约 {fmt_pct(float(btc_ath))}。"
        )
    body += (
        "口径为 CoinGecko 公开分类与市值加权涨跌，非 Glassnode 专有板块指数；数字可能与原文略有差异。"
        f"\n{DASHBOARD_URL}"
    )
    title = "隐私币板块市值与 ZEC 占比"
    return msg_id, f"{title}\n{body}"


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(time.time()))
        digest = hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(digest).decode("utf-8")
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
            raise RuntimeError(result)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": [], "updatedAt": ""}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": [], "updatedAt": ""}
    ids = doc.get("sentIds") if isinstance(doc, dict) else None
    return {
        "sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else [],
        "updatedAt": str(doc.get("updatedAt") or "") if isinstance(doc, dict) else "",
    }


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    path = resolve_json(args.json or None)
    doc = json.loads(path.read_text(encoding="utf-8"))
    msg_id, raw = build_news(doc)
    text = as_news(raw)
    print(f"[privacy-coins] id={msg_id}\n{text}")

    if not args.push:
        print("\n[dry-run]")
        return 0

    state = load_state()
    if msg_id in state["sentIds"] and not args.force:
        print(f"skip {msg_id}")
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
    except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
        print(f"[ERROR] Lark: {exc}", file=sys.stderr)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"=== {stamp} {msg_id} ok=False {exc}\n{text}\n\n")
        return 1

    print(f"  → Lark OK {msg_id}")
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-60:]
    save_state(state)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"=== {stamp} {msg_id} ok=True\n{text}\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
