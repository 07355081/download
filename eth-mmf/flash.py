# -*- coding: utf-8 -*-
"""BENJI 在 Ethereum 上超过 BUIDL → 新闻稿。

只在领先关系从 BUIDL 变成 BENJI 时发最新一条。冷启动不发。不回扫 5 月。
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
    HERE / "output" / "json" / "eth_mmf.json",
    Path("/root/dashboard/public/json/eth-mmf/eth_mmf.json"),
]
STATE_PATH = HERE / "output" / "eth_mmf_lark.json"
LOG_PATH = HERE / "eth_mmf_flash.log"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/eth-mmf"
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


def load_doc(explicit: str) -> tuple[Path, dict[str, Any]]:
    paths = [Path(explicit)] if explicit else JSON_CANDIDATES
    for path in paths:
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("funds"), list):
                return path, doc
    raise FileNotFoundError("找不到 eth_mmf.json")


def fund(doc: dict[str, Any], ticker: str) -> dict[str, Any]:
    for row in doc.get("funds") or []:
        if isinstance(row, dict) and row.get("ticker") == ticker:
            return row
    raise KeyError(ticker)


def usd_zh(amount: float) -> str:
    if amount >= 100_000_000:
        value = amount / 100_000_000
        text = f"{value:.2f}".rstrip("0").rstrip(".")
        return f"{text} 亿美元"
    value = amount / 10_000
    text = f"{value:.0f}" if abs(value - round(value)) < 0.05 else f"{value:.1f}"
    return f"{text} 万美元"


def cn_day(stamp: str) -> str:
    day = stamp[:10]
    y, m, d = day.split("-")
    return f"{y} 年 {int(m)} 月 {int(d)} 日"


def build_news(doc: dict[str, Any]) -> str:
    benji = fund(doc, "BENJI")
    buidl = fund(doc, "BUIDL")
    day = cn_day(str(doc.get("updated_at") or datetime.now(timezone.utc).isoformat()))
    text = (
        f"吴说获悉，据吴说数据中心统计，截至 {day}，"
        f"Franklin Templeton 旗下 BENJI 在 Ethereum 上的链上市值约 {usd_zh(float(benji['eth_usd']))}"
    )
    chg = benji.get("eth_chg_30d_pct")
    if isinstance(chg, (int, float)) and abs(chg) >= 5:
        verb = "增长" if chg > 0 else "回撤"
        text += f"，过去一个月{verb}约 {abs(chg):.0f}%"
    text += (
        f"，超过 BlackRock BUIDL 的约 {usd_zh(float(buidl['eth_usd']))}。"
    )
    if str(doc.get("top_eth")) == "BENJI":
        text += "在所统计的代币化货基中，BENJI 为 Ethereum 上规模最大。"
    return f"{text}\n{PAGE}"


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
        body = json.loads(resp.read().decode("utf-8"))
    if str(body.get("code", "0")) != "0":
        raise RuntimeError(body)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": [], "lastLeader": None}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": [], "lastLeader": None}
    if not isinstance(doc, dict):
        return {"sentIds": [], "lastLeader": None}
    ids = doc.get("sentIds")
    return {
        "sentIds": [item for item in ids if isinstance(item, str)] if isinstance(ids, list) else [],
        "lastLeader": doc.get("lastLeader") if isinstance(doc.get("lastLeader"), str) else None,
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
    try:
        path, doc = load_doc(args.json)
        benji = float(fund(doc, "BENJI")["eth_usd"])
        buidl = float(fund(doc, "BUIDL")["eth_usd"])
    except (OSError, json.JSONDecodeError, FileNotFoundError, KeyError, TypeError, ValueError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    leader = "BENJI" if benji > buidl else "BUIDL"
    day = str(doc.get("updated_at") or "")[:10]
    msg_id = f"eth-mmf-benji-leads-{day}"
    state = load_state()
    prev = state.get("lastLeader")
    crossed = prev == "BUIDL" and leader == "BENJI"
    print(
        f"[eth-mmf] json={path} BENJI={benji:.0f} BUIDL={buidl:.0f} "
        f"leader={leader} prev={prev} crossed={crossed}"
    )
    if not crossed and not args.force:
        print("[skip] 未出现 BENJI 超过 BUIDL，不发")
        if args.push:
            state["lastLeader"] = leader
            save_state(state)
        return 0
    text = build_news(doc)
    print("---")
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
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
        state["lastLeader"] = leader
        save_state(state)
        return 0
    try:
        send_lark(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Lark 失败: {exc}", file=sys.stderr)
        return 1
    print(f"  → Lark OK {msg_id}")
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-40:]
    state["lastLeader"] = leader
    save_state(state)
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} id={msg_id}\n{text}\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
