#!/usr/bin/env python3
"""STRC 破面值 + apxUSD 市值回撤 → Lark 新闻体。

过线（只发最新，不回扫）：
- STRC 从上方向下穿过 100 美元面值（已在下方不补发；回到上方后可再次触发）
- 或 apxUSD 近 7 日流通市值变化 ≤ -1 亿美元
- 或较序列峰值回撤刚跌破 20%（已深回撤不天天发；回升到 -20% 以内后可再触发）

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
    HERE / "output" / "json" / "strc_apxusd.json",
    Path("/root/dashboard/public/json/strc-apxusd/strc_apxusd.json"),
]
STATE_PATH = HERE / "output" / "strc_apxusd_lark.json"
LOG_PATH = HERE / "flash.log"
JSON_URL = "https://data.wublock123.com/dashboard/treasuries"
PAGE = "https://data.wublock123.com/dashboard/treasuries"
PAR = 100.0
WOW_USD = -100_000_000.0
DD_PCT = -20.0
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
    empty = {"sentIds": [], "strc_armed": True, "dd_armed": True, "last_price": None}
    if not STATE_PATH.is_file():
        return dict(empty)
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(empty)
    if not isinstance(doc, dict):
        return dict(empty)
    ids = doc.get("sentIds")
    # 旧 state 无 dd_armed 时：若已有 last_price，视为已初始化，勿默认 True 以免天天误报深回撤
    if "dd_armed" in doc:
        dd_armed = bool(doc.get("dd_armed"))
    else:
        dd_armed = doc.get("last_price") is None
    return {
        "sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else [],
        "strc_armed": bool(doc.get("strc_armed", True)),
        "dd_armed": dd_armed,
        "last_price": doc.get("last_price"),
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
            if isinstance(doc, dict) and isinstance(doc.get("strc"), dict):
                print(f"[strc-apxusd] json={path}")
                return doc
    raise FileNotFoundError("找不到 strc_apxusd.json")


def yi(usd: float) -> str:
    if abs(usd) >= 1e8:
        return f"{usd / 1e8:.2f} 亿美元"
    return f"{usd / 1e4:.0f} 万美元"


def pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def build_news(doc: dict[str, Any], reasons: list[str]) -> str:
    strc = doc["strc"]
    apx = doc["apxusd"]
    now = datetime.now(TZ_CN)
    stamp = f"{now.year} 年 {now.month} 月 {now.day} 日 {now.hour:02d}:{now.minute:02d}"
    lead = "；".join(reasons) if reasons else "相关指标过线"
    return (
        f"吴说获悉，据吴说数据中心统计，截至 {stamp}（UTC+8），{lead}。"
        f"Strategy 优先股 STRC 现报约 {float(strc['price']):.2f} 美元"
        f"{'，低于 100 美元面值' if strc.get('below_par') else ''}。"
        f"Apyx 稳定币 apxUSD 流通市值约 {yi(float(apx['mcap_usd']))}"
        f"（较序列峰值 {yi(float(apx['ath_mcap_usd']))}（{apx['ath_day']}）约 "
        f"{pct(float(apx['drawdown_from_ath_pct'] or 0))}；"
        f"近 7 日约 {pct(float(apx['chg_7d_pct'] or 0))} / "
        f"{yi(float(apx['chg_7d_usd']))}）。"
        f"口径为 Yahoo STRC 与 DefiLlama apxUSD 流通市值，非 Dune Gate Research。"
        f"\n{PAGE}"
    )


def evaluate(doc: dict[str, Any], state: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    strc = doc["strc"]
    apx = doc["apxusd"]
    price = float(strc["price"])
    reasons: list[str] = []
    cold = state.get("last_price") is None

    # 冷启动：已在面值下方则只布防，不补发「跌破」
    if cold and price < PAR:
        state["strc_armed"] = False
    armed = bool(state.get("strc_armed", True))
    if price >= PAR:
        armed = True
    elif armed and price < PAR:
        reasons.append(f"STRC 跌破 {PAR:.0f} 美元面值")
        armed = False
    state["strc_armed"] = armed
    state["last_price"] = price

    wow = float(apx.get("chg_7d_usd") or 0)
    if wow <= WOW_USD:
        reasons.append(f"apxUSD 近 7 日流通市值约 {yi(wow)}")

    dd = apx.get("drawdown_from_ath_pct")
    dd_f = float(dd) if isinstance(dd, (int, float)) else None
    dd_armed = bool(state.get("dd_armed", True))
    if dd_f is None:
        pass
    elif dd_f > DD_PCT:
        dd_armed = True
    elif cold and dd_f <= DD_PCT:
        # 冷启动已深回撤：布防关闭，不补发
        dd_armed = False
    elif dd_armed and dd_f <= DD_PCT:
        reasons.append(f"apxUSD 较峰值回撤约 {pct(dd_f)}")
        dd_armed = False
    state["dd_armed"] = dd_armed
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
    day = datetime.now(TZ_CN).strftime("%Y-%m-%d")
    msg_id = f"strc-apxusd-{day}"
    print(
        f"[strc-apxusd] STRC={doc['strc']['price']} armed={state['strc_armed']} "
        f"reasons={reasons}"
    )
    if not reasons and not args.force:
        save_state(state)
        print("[skip] 未过线")
        return 0

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
