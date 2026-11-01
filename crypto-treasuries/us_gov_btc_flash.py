# -*- coding: utf-8 -*-
"""美国政府 BTC 持仓变动 → Lark。数据即 /dashboard/treasuries 政府页。

持仓表不是当日流水，不发日常。只有相对上次记录变动达到 1000 枚才发【历史阈值警报】。
第一次只记基线，不发。
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CANDIDATES = [
    HERE / "output" / "json" / "overview_bitcoin.json",
    Path("/root/dashboard/public/json/crypto-treasuries/overview_bitcoin.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "crypto-treasuries" / "overview_bitcoin.json",
]
STATE_PATH = HERE / "output" / "us_gov_btc_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
PAGE = "https://data.wublock123.com/dashboard/treasuries"
MIN_BTC = 1000.0


def _ensure_lark_copy() -> None:
    import sys as _sys
    from pathlib import Path as _Path
    folder = _Path(__file__).resolve().parent
    while folder != folder.parent and not (folder / "lark_copy.py").is_file():
        folder = folder.parent
    if str(folder) not in _sys.path:
        _sys.path.insert(0, str(folder))


_ensure_lark_copy()
from lark_copy import as_news

def env() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            out[key] = os.environ[key]
    return out


def send(webhook: str, secret: str | None, text: str) -> None:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(
            hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
        ).decode()
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode() or "{}")
    if str(body.get("code", "0")) != "0":
        raise RuntimeError(body)


def load_doc() -> dict[str, Any]:
    for path in CANDIDATES:
        if path.is_file():
            print(f"[us-gov] {path}")
            doc = json.loads(path.read_text(encoding="utf-8"))
            data = doc.get("data") if isinstance(doc, dict) else None
            return data if isinstance(data, dict) else doc
    raise FileNotFoundError("找不到 overview_bitcoin.json")


def us_holdings(doc: dict[str, Any]) -> float:
    for row in doc.get("governments") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        if name.lower() in {"united states", "usa", "u.s.", "united states of america"}:
            return float(row["total_holdings"])
    raise RuntimeError("政府列表里没有 United States")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    holdings = us_holdings(load_doc())
    state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.is_file() else {}
    previous = state.get("holdings")
    print(f"holdings={holdings} previous={previous}")
    if previous is None:
        state["holdings"] = holdings
        state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if args.push:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("[baseline] 第一次只记账，不发")
        return 0
    delta = holdings - float(previous)
    if abs(delta) < MIN_BTC:
        print(f"[skip] 变动 {delta:.0f} 枚，未到 {MIN_BTC:.0f}")
        return 0
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    msg_id = f"us-gov-btc-{day}-{int(holdings)}"
    sent = state.get("sentIds") if isinstance(state.get("sentIds"), list) else []
    text = (
        "【历史阈值警报】\n"
        f"据吴说数据中心统计，美国政府相关 BTC 最新约 {holdings:,.0f} 枚，"
        f"较上次记录 {float(previous):,.0f} 枚变动 {delta:+,.0f} 枚。"
        "这是持仓表快照，不是当日转出流水，也不含短期持有者亏损入所。\n"
        f"{PAGE}"
    )
    print(msg_id)
    print(text)
    if not args.push:
        return 0
    if msg_id not in sent:
        conf = env()
        send(conf["LARK_ALERT_WEBHOOK"], conf.get("LARK_ALERT_SECRET") or None, text)
        sent.append(msg_id)
        print("Lark OK", msg_id)
    state.update(
        {
            "holdings": holdings,
            "sentIds": sent,
            "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
