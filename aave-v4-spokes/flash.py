#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Aave V4 存款 ATH / 显著变化 → Lark 新闻体。

过线（只发最新，不回扫）：
- 官方 GraphQL 协议存款创本通道已记录历史新高（首日只建基线不推）
- 或近 7 日存款变化绝对值 ≥ 15%

正文含借款、Ethereum 占比、Bluechip/EtherFi Spoke、外汇+黄金 Spoke 存款。
非 JackMandin；数字可能与外电差一个数量级。

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
    HERE / "output" / "json" / "aave_v4_spokes.json",
    Path("/root/dashboard/public/json/aave-v4-spokes/aave_v4_spokes.json"),
]
STATE_PATH = HERE / "output" / "aave_v4_spokes_lark.json"
LOG_PATH = HERE / "flash.log"
JSON_URL = "https://data.wublock123.com/dashboard/lark-feeds/aave-v4-spokes"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/aave-v4-spokes"
CHG7_MIN = 15.0
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
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": as_news(text)}}
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
    empty = {"sentIds": [], "baseline": False, "ath_armed": True, "chg7_key": None}
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
        "chg7_key": doc.get("chg7_key"),
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
                print(f"[aave-v4-spokes] json={path}")
                return doc
    raise FileNotFoundError("找不到 aave_v4_spokes.json")


def yi(usd: float | None) -> str:
    if usd is None:
        return "—"
    if abs(usd) >= 1e8:
        return f"{usd / 1e8:.2f} 亿美元"
    if abs(usd) >= 1e4:
        return f"{usd / 1e4:.0f} 万美元"
    return f"{usd:,.0f} 美元"


def pct(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def spoke_bit(named: dict[str, Any]) -> str:
    bits: list[str] = []
    for key, label in (("bluechip", "Bluechip Spoke"), ("etherfi", "EtherFi Spoke")):
        item = named.get(key)
        if isinstance(item, dict) and item.get("supplied_usd") is not None:
            bits.append(f"{label} 存款约 {yi(float(item['supplied_usd']))}")
    fx = named.get("forex") if isinstance(named.get("forex"), dict) else None
    gold = named.get("gold") if isinstance(named.get("gold"), dict) else None
    paxg = named.get("paxg") if isinstance(named.get("paxg"), dict) else None
    fx_gold = 0.0
    for item in (fx, gold, paxg):
        if item and item.get("supplied_usd") is not None:
            fx_gold += float(item["supplied_usd"])
    if fx_gold > 0:
        detail = []
        if fx:
            detail.append(f"外汇 {yi(float(fx['supplied_usd']))}")
        if gold:
            detail.append(f"黄金 {yi(float(gold['supplied_usd']))}")
        if paxg:
            detail.append(f"PAXG {yi(float(paxg['supplied_usd']))}")
        bits.append(f"外汇与黄金相关 Spoke 合计约 {yi(fx_gold)}（{'、'.join(detail)}）")
    return "；".join(bits) if bits else "Spoke 结构暂未取到"


def build_news(doc: dict[str, Any], reasons: list[str]) -> str:
    latest = doc["latest"]
    named = latest.get("named_spokes") or {}
    now = datetime.now(TZ_CN)
    stamp = f"{now.year} 年 {now.month} 月 {now.day} 日 {now.hour:02d}:{now.minute:02d}"
    day = latest.get("asof_day") or "最新"
    eth = latest.get("eth_share_of_deposits_pct")
    if eth is None:
        eth = latest.get("eth_share_llama_pct")
    if eth is None:
        eth = latest.get("eth_share_hub_pct")
    lead = "；".join(reasons) if reasons else "相关指标过线"
    llama = doc.get("llama") or {}
    llama_bit = ""
    if llama.get("tvl_usd") is not None:
        llama_bit = (
            f"DefiLlama aave-v4 TVL 近邻约 {yi(float(llama['tvl_usd']))}"
            f"（借款约 {yi(fnum(llama.get('borrowed_usd')))}）。"
        )
    return (
        f"吴说获悉，据吴说数据中心统计，截至 {stamp}（UTC+8），{lead}。"
        f"Aave V4 官方 GraphQL 口径下，{day} 协议存款约 {yi(fnum(latest.get('deposits_usd')))}，"
        f"借款约 {yi(fnum(latest.get('borrows_usd')))}；"
        f"Ethereum 相关 Hub 存款约占 {pct(eth) if eth is not None else '—'}。"
        f"{spoke_bit(named)}。"
        f"{llama_bit}"
        f"口径为 Aave 官方 GraphQL + DefiLlama aave-v4，非 JackMandin；"
        f"数字可能与外电略有差异。"
        f"\n{PAGE}"
    )


def fnum(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def evaluate(doc: dict[str, Any], state: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    latest = doc["latest"]
    history = [h for h in (doc.get("history") or []) if isinstance(h, dict)]
    deposits = fnum(latest.get("deposits_usd"))
    day = str(latest.get("asof_day") or "")
    reasons: list[str] = []

    prior = [
        float(h["deposits_usd"])
        for h in history
        if str(h.get("day")) != day and isinstance(h.get("deposits_usd"), (int, float))
    ]
    prior_high = max(prior) if prior else None

    # 首日：只建基线（即使当前已是 ATH / 近 7 日已大涨也不补发）
    if not state.get("baseline"):
        state["baseline"] = True
        state["ath_armed"] = False if (
            deposits is not None and prior_high is not None and deposits >= prior_high - 1e-6
        ) else True
        chg0 = fnum(latest.get("deposits_chg_7d_pct"))
        if chg0 is not None and abs(chg0) >= CHG7_MIN and day:
            state["chg7_key"] = f"{day}:{chg0:+.1f}"
        return [], state

    ath_armed = bool(state.get("ath_armed", True))
    if deposits is None:
        pass
    elif prior_high is None:
        state["ath_armed"] = True
    elif deposits < prior_high - 1.0:
        ath_armed = True
    elif ath_armed and deposits > prior_high + 1.0:
        reasons.append(f"Aave V4 协议存款创本通道新高，约 {yi(deposits)}")
        ath_armed = False
    state["ath_armed"] = ath_armed

    chg = fnum(latest.get("deposits_chg_7d_pct"))
    if chg is not None and abs(chg) >= CHG7_MIN:
        key = f"{day}:{chg:+.1f}"
        if state.get("chg7_key") != key:
            reasons.append(f"近 7 日存款约 {pct(chg)}")
            state["chg7_key"] = key

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
    day = str(latest.get("asof_day") or datetime.now(TZ_CN).date().isoformat())
    print(
        f"[aave-v4-spokes] day={day} dep={latest.get('deposits_usd')} "
        f"ath={latest.get('deposits_is_ath')} chg7={latest.get('deposits_chg_7d_pct')} "
        f"reasons={reasons} baseline={state.get('baseline')}"
    )
    if not reasons and not args.force:
        save_state(state)
        print("[skip] 未过线" if state.get("baseline") else "[skip] 首日建基线")
        return 0

    # 稳定 id：按日+原因类型
    tags: list[str] = []
    blob = "｜".join(reasons)
    if "新高" in blob:
        tags.append("ath")
    if "近 7 日" in blob:
        tags.append("chg7")
    if not tags:
        tags.append("force")
    msg_id = f"aave-v4-spokes-{day}-{'-'.join(tags)}"

    text = as_news(build_news(doc, reasons or ["强制推送"]))
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
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-120:]
    save_state(state)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} {msg_id} ok=True\n{text}\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
