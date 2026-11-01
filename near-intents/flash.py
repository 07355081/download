# -*- coding: utf-8 -*-
"""NEAR Intents → Lark 新闻体，只推最新一条。"""
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
    HERE / "output" / "json" / "near_intents.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "near-intents" / "near_intents.json",
    Path("/root/dashboard/public/json/near-intents/near_intents.json"),
]
STATE_PATH = HERE / "output" / "near_intents_lark.json"
LOG_PATH = HERE / "flash.log"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/lark-feeds/near-intents"

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
    raise FileNotFoundError("找不到 near_intents.json")


def fmt_yi(usd: float | None) -> str:
    if usd is None:
        return "—"
    yi = float(usd) / 1e8
    if abs(yi) >= 100:
        return f"{yi:.0f} 亿美元"
    if abs(yi) >= 10:
        return f"{yi:.1f} 亿美元"
    if abs(yi) >= 1:
        return f"{yi:.2f} 亿美元"
    return f"{float(usd) / 1e4:.0f} 万美元"


def fmt_wan(usd: float | None) -> str:
    if usd is None:
        return "—"
    return f"{float(usd) / 1e4:.0f} 万美元"


def build_news(doc: dict[str, Any]) -> tuple[str, str]:
    day = str(doc.get("as_of") or "")[:10] or datetime.now(TZ_CN).strftime("%Y-%m-%d")
    msg_id = f"near-intents-daily-{day}"
    y, m, d = day.split("-")
    tvl = doc.get("tvl_usd")
    vol = doc.get("volume") or {}
    focus = doc.get("tokens_focus_usd") or {}

    bits = []
    for sym in ("NEAR", "ZEC", "USDC", "USDT"):
        if sym in focus:
            bits.append(f"{sym} 约 {fmt_wan(float(focus[sym]))}")
    token_s = "，".join(bits) if bits else "代币拆分暂缺"

    body = (
        f"据吴说数据中心统计，截至 {int(y)} 年 {int(m)} 月 {int(d)} 日，"
        f"DefiLlama 口径下 NEAR Intents TVL 约 {fmt_yi(float(tvl) if tvl is not None else None)}；"
        f"其中 {token_s}。"
        f"累计交易量约 {fmt_yi(float(vol['total_all_usd']) if vol.get('total_all_usd') is not None else None)}，"
        f"过去 7 天约 {fmt_yi(float(vol['total7d_usd']) if vol.get('total7d_usd') is not None else None)}，"
        f"近 24 小时约 {fmt_yi(float(vol['total24h_usd']) if vol.get('total24h_usd') is not None else None)}。"
        f"口径为 DefiLlama，非 Dune Near 官方面板；TVL/成交数字可能与原文不一致。"
        f"\n{DASHBOARD_URL}"
    )
    return msg_id, f"NEAR Intents TVL 与交易量\n{body}"


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
        return {"sentIds": []}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": []}
    ids = doc.get("sentIds") if isinstance(doc, dict) else []
    return {"sentIds": [str(x) for x in ids] if isinstance(ids, list) else []}


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
    print(f"[near-intents] id={msg_id}\n{text}")

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
