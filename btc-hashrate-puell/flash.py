#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BTC 算力 7 日均 / Puell → Lark 新闻体。

过线（只发最新，不回扫）：
- 算力 7 日均创本通道已记录新高（首日建基线不推）
- 或较一周前 |Δ| ≥ 40 EH/s

正文附 Puell（有则写）。不含矿工持仓总量。

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
    HERE / "output" / "json" / "btc_hashrate_puell.json",
    Path("/root/dashboard/public/json/btc-hashrate-puell/btc_hashrate_puell.json"),
]
STATE_PATH = HERE / "output" / "btc_hashrate_puell_lark.json"
LOG_PATH = HERE / "flash.log"
JSON_URL = "https://data.wublock123.com/dashboard/mining_shutdown"
PAGE = "https://data.wublock123.com/dashboard/mining_shutdown"
WOW_EH = 40.0
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
    empty = {"sentIds": [], "baseline": False, "ath_armed": True, "wow_key": None}
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
        "sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else [],
        "baseline": bool(doc.get("baseline")),
        "ath_armed": bool(doc.get("ath_armed", True)),
        "wow_key": doc.get("wow_key"),
    }


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
            if isinstance(doc, dict) and isinstance(doc.get("latest"), dict):
                print(f"[btc-hashrate-puell] json={path}")
                return doc
    raise FileNotFoundError("找不到 btc_hashrate_puell.json")


def eh(v: float) -> str:
    return f"{v:,.2f} EH/s"


def build_news(latest: dict[str, Any], reasons: list[str]) -> str:
    now = datetime.now(TZ_CN)
    stamp = f"{now.year} 年 {now.month} 月 {now.day} 日 {now.hour:02d}:{now.minute:02d}"
    day = latest.get("day") or "最新"
    lead = "；".join(reasons) if reasons else "相关指标过线"
    bits = [
        f"比特币全网算力 7 日均约 {eh(float(latest['hashrate_7d_eh']))}",
    ]
    wow = latest.get("wow_eh")
    if isinstance(wow, (int, float)):
        sign = "+" if wow >= 0 else "−"
        bits.append(f"较一周前约 {sign}{eh(abs(float(wow)))}")
    puell = latest.get("puell")
    if isinstance(puell, (int, float)):
        pbit = f"Puell Multiple 约 {float(puell):.2f}"
        pw = latest.get("puell_wow")
        if isinstance(pw, (int, float)):
            pbit += f"（较一周前约 {pw:+.2f}）"
        bits.append(pbit)
    return (
        f"吴说获悉，据吴说数据中心统计，截至 {stamp}（UTC+8），{lead}。"
        f"{day}：{'；'.join(bits)}。"
        f"算力口径为 mempool.space 日序列 7 日均，Puell 为 CoinGlass；"
        f"不含 CryptoQuant 矿工持仓总量；数字可能与外电略有差异。"
        f"\n{PAGE}"
    )


def evaluate(doc: dict[str, Any], state: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    latest = doc["latest"]
    history = [h for h in (doc.get("history") or []) if isinstance(h, dict)]
    day = str(latest.get("day") or "")
    ma = float(latest["hashrate_7d_eh"])
    reasons: list[str] = []

    prior = [
        float(h["hashrate_7d_eh"])
        for h in history
        if str(h.get("day")) != day and isinstance(h.get("hashrate_7d_eh"), (int, float))
    ]
    prior_high = max(prior) if prior else None

    if not state.get("baseline"):
        state["baseline"] = True
        state["ath_armed"] = False if (
            prior_high is not None and ma >= prior_high - 1e-6
        ) else True
        wow0 = latest.get("wow_eh")
        if isinstance(wow0, (int, float)) and abs(float(wow0)) >= WOW_EH and day:
            state["wow_key"] = f"{day}:{float(wow0):+.1f}"
        return [], state

    ath_armed = bool(state.get("ath_armed", True))
    if prior_high is None:
        state["ath_armed"] = True
    elif ma < prior_high - 0.01:
        ath_armed = True
    elif ath_armed and ma > prior_high + 0.01:
        reasons.append(f"全网算力 7 日均创本通道新高，约 {eh(ma)}")
        ath_armed = False
    state["ath_armed"] = ath_armed

    wow = latest.get("wow_eh")
    if isinstance(wow, (int, float)) and abs(float(wow)) >= WOW_EH:
        key = f"{day}:{float(wow):+.1f}"
        if state.get("wow_key") != key:
            sign = "+" if wow >= 0 else "−"
            reasons.append(f"算力 7 日均较一周前约 {sign}{eh(abs(float(wow)))}")
            state["wow_key"] = key

    return reasons, state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        doc = load_doc()
    except (OSError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    state = load_state()
    reasons, state = evaluate(doc, state)
    latest = doc["latest"]
    day = str(latest.get("day") or datetime.now(TZ_CN).date().isoformat())
    print(
        f"[btc-hashrate-puell] day={day} ma7={latest.get('hashrate_7d_eh')} "
        f"wow={latest.get('wow_eh')} reasons={reasons} baseline={state.get('baseline')}"
    )
    if not reasons and not args.force:
        save_state(state)
        print("[skip] 未过线" if state.get("baseline") else "[skip] 首日建基线")
        return 0

    tags: list[str] = []
    blob = "｜".join(reasons)
    if "新高" in blob:
        tags.append("ath")
    if "一周前" in blob:
        tags.append("wow")
    if not tags:
        tags.append("force")
    msg_id = f"btc-hashrate-puell-{day}-{'-'.join(tags)}"
    text = as_news(build_news(latest, reasons or ["强制推送"]))
    print("---")
    print(f"id={msg_id}")
    print(text)
    if not args.push:
        save_state(state)
        print("\n[dry-run] 未推送")
        return 0

    if msg_id in state["sentIds"] and not args.force:
        save_state(state)
        print(f"[skip] already {msg_id}")
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
    state["sentIds"] = (state["sentIds"] + [msg_id])[-120:]
    save_state(state)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} {msg_id} ok=True\n{text}\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
