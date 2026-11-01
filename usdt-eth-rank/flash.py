# -*- coding: utf-8 -*-
"""USDT 市值超过 ETH → Lark 新闻体。

仅在从上方向下：ETH 更大 → USDT 更大 时发最新一次。已领先不补发。
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
    HERE / "output" / "json" / "usdt_eth_rank.json",
    Path("/root/dashboard/public/json/usdt-eth-rank/usdt_eth_rank.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "usdt-eth-rank" / "usdt_eth_rank.json",
]
STATE_PATH = HERE / "output" / "usdt_eth_rank_lark.json"
PAGE = "https://data.wublock123.com/dashboard/stablecoin-marketcap"
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
    raise FileNotFoundError("找不到 usdt_eth_rank.json")


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
    empty = {"sentIds": [], "armed": True, "last_over": None}
    if not STATE_PATH.is_file():
        return dict(empty)
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(empty)
    if not isinstance(doc, dict):
        return dict(empty)
    ids = doc.get("sentIds")
    return {
        "sentIds": [item for item in ids if isinstance(item, str)] if isinstance(ids, list) else [],
        "armed": bool(doc.get("armed", True)),
        "last_over": doc.get("last_over"),
    }


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def yi(usd: float) -> str:
    return f"{usd / 1e8:.0f} 亿美元" if abs(usd) >= 1e10 else f"{usd / 1e8:.1f} 亿美元"


def build_news(doc: dict[str, Any]) -> str:
    usdt = doc["usdt"]
    eth = doc["eth"]
    btc = doc["btc"]
    rank = usdt.get("rank")
    rank_bit = f"暂居全球加密货币市值第 {rank}" if rank else "市值超过 Ethereum"
    return (
        "吴说获悉，据吴说数据中心统计，"
        f"Tether（USDT）市值约 {yi(float(usdt['mcap_usd']))}，"
        f"Ethereum 约 {yi(float(eth['mcap_usd']))}，"
        f"USDT 已超过 ETH，{rank_bit}；"
        f"比特币约 {yi(float(btc['mcap_usd']))}。"
        "口径为 CoinGecko 流通市值，非 The Block。"
        f"\n{PAGE}"
    )


def evaluate(doc: dict[str, Any], state: dict[str, Any]) -> bool:
    over = bool(doc.get("usdt_over_eth"))
    cold = state.get("last_over") is None
    if cold and over:
        state["armed"] = False
        state["last_over"] = over
        return False
    if not over:
        state["armed"] = True
        state["last_over"] = over
        return False
    armed = bool(state.get("armed", True))
    state["last_over"] = over
    if armed:
        state["armed"] = False
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    doc = json.loads(resolve_json().read_text(encoding="utf-8"))
    state = load_state()
    fire = evaluate(doc, state)
    day = str(doc.get("asof_day") or datetime.now(TZ_CN).date())
    msg_id = f"usdt-over-eth-{day}"
    print(
        f"[usdt-eth-rank] over={doc.get('usdt_over_eth')} armed={state['armed']} "
        f"fire={fire} {msg_id}"
    )
    if not fire:
        save_state(state)
        print("[skip] USDT 未从上方向下超过 ETH，不补历史")
        return 0
    text = as_news(build_news(doc))
    print(msg_id)
    print(text)
    if msg_id in state["sentIds"]:
        save_state(state)
        print(f"[skip] 已发 {msg_id}")
        return 0
    if not args.push:
        save_state(state)
        print("[dry-run] 未推送")
        return 0
    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    send_lark(webhook, secret, text)
    state["sentIds"] = (state["sentIds"] + [msg_id])[-40:]
    save_state(state)
    print(f"[pushed] {msg_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
