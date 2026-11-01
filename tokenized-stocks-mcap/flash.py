#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代币化股票市值（CoinGecko）→ Lark 新闻体。

过线（只发最新，不回扫）：
- 总市值创本通道已记录新高（首日只建基线不推）
- 或较昨日 |DoD| ≥ 8%

正文写 Top 链份额；口径为 CoinGecko，非 Token Terminal。

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
    HERE / "output" / "json" / "tokenized_stocks_mcap.json",
    Path("/root/dashboard/public/json/tokenized-stocks-mcap/tokenized_stocks_mcap.json"),
]
STATE_PATH = HERE / "output" / "tokenized_stocks_mcap_lark.json"
LOG_PATH = HERE / "flash.log"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/tokenized-stocks-mcap"
DOD_MIN = 8.0
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
    empty = {"sentIds": [], "baseline": False, "ath_armed": True, "dod_key": None}
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
        "dod_key": doc.get("dod_key"),
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
                print(f"[tokenized-stocks-mcap] json={path}")
                return doc
    raise FileNotFoundError("找不到 tokenized_stocks_mcap.json")


def fnum(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


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


def chain_bit(latest: dict[str, Any]) -> str:
    chains = latest.get("chains") or []
    bits: list[str] = []
    for row in chains[:5]:
        if not isinstance(row, dict):
            continue
        label = row.get("label") or row.get("id")
        bits.append(f"{label} 约 {yi(fnum(row.get('mcap_usd')))}（约占 {pct(fnum(row.get('share_pct')))}）")
    top3 = fnum(latest.get("top3_share_pct"))
    head = "；".join(bits) if bits else "分链暂缺"
    if top3 is not None and bits:
        return f"{head}。前三条约占 {pct(top3)}"
    return head


def build_news(doc: dict[str, Any], reasons: list[str]) -> str:
    latest = doc["latest"]
    now = datetime.now(TZ_CN)
    stamp = f"{now.year} 年 {now.month} 月 {now.day} 日 {now.hour:02d}:{now.minute:02d}"
    day = latest.get("asof_day") or "最新"
    lead = "；".join(reasons) if reasons else "相关指标过线"
    n = latest.get("n_assets")
    method = latest.get("chain_method") or "近似"
    return (
        f"吴说获悉，据吴说数据中心统计，截至 {stamp}（UTC+8），{lead}。"
        f"CoinGecko「Tokenized Stock」口径下，{day} 代币化股票合计市值约 {yi(fnum(latest.get('mcap_usd')))}"
        f"（收录约 {n} 只）。"
        f"{chain_bit(latest)}。"
        f"日环比约 {pct(fnum(latest.get('dod_pct')))}。"
        f"口径为 CoinGecko 公开分类市值，分链按合约平台或名称启发式（{method}），"
        f"非 Token Terminal；数字可能与外电略有差异。"
        f"\n{PAGE}"
    )


def evaluate(doc: dict[str, Any], state: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    latest = doc["latest"]
    history = [h for h in (doc.get("history") or []) if isinstance(h, dict)]
    total = fnum(latest.get("mcap_usd"))
    day = str(latest.get("asof_day") or "")
    reasons: list[str] = []

    prior = [
        float(h["mcap_usd"])
        for h in history
        if str(h.get("day")) != day and isinstance(h.get("mcap_usd"), (int, float))
    ]
    prior_high = max(prior) if prior else None

    if not state.get("baseline"):
        state["baseline"] = True
        state["ath_armed"] = False if (
            total is not None and prior_high is not None and total >= prior_high - 1e-6
        ) else True
        dod0 = fnum(latest.get("dod_pct"))
        if dod0 is not None and abs(dod0) >= DOD_MIN and day:
            state["dod_key"] = f"{day}:{dod0:+.1f}"
        return [], state

    ath_armed = bool(state.get("ath_armed", True))
    if total is None:
        pass
    elif prior_high is None:
        state["ath_armed"] = True
    elif total < prior_high - 1.0:
        ath_armed = True
    elif ath_armed and total > prior_high + 1.0:
        reasons.append(f"代币化股票合计市值创本通道新高，约 {yi(total)}")
        ath_armed = False
    state["ath_armed"] = ath_armed

    dod = fnum(latest.get("dod_pct"))
    if dod is not None and abs(dod) >= DOD_MIN:
        key = f"{day}:{dod:+.1f}"
        if state.get("dod_key") != key:
            reasons.append(f"合计市值较昨日约 {pct(dod)}")
            state["dod_key"] = key

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
        f"[tokenized-stocks-mcap] day={day} mcap={latest.get('mcap_usd')} "
        f"ath={latest.get('mcap_is_ath')} dod={latest.get('dod_pct')} "
        f"reasons={reasons} baseline={state.get('baseline')}"
    )
    if not reasons and not args.force:
        save_state(state)
        print("[skip] 未过线" if state.get("baseline") else "[skip] 首日建基线")
        return 0

    tags: list[str] = []
    blob = "｜".join(reasons)
    if "新高" in blob:
        tags.append("ath")
    if "昨日" in blob:
        tags.append("dod")
    if not tags:
        tags.append("force")
    msg_id = f"tokenized-stocks-mcap-{day}-{'-'.join(tags)}"

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
