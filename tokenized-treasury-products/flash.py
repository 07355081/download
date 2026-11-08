#!/usr/bin/env python3
"""代币化国债产品市值榜（CoinGecko）→ Lark 新闻体。

只发最新快照：
- 首日建基线不推
- 合计创本通道新高，或 |日环比|≥8%，或前三 ticker 组合变化

非 Token Terminal；不补外电历史窗。

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
    HERE / "output" / "json" / "tokenized_treasury_products.json",
    Path("/root/dashboard/public/json/tokenized-treasury-products/tokenized_treasury_products.json"),
]
STATE_PATH = HERE / "output" / "tokenized_treasury_products_lark.json"
LOG_PATH = HERE / "flash.log"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/tokenized-treasury-products"
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
        raw = resp.read().decode("utf-8", errors="replace")
    if raw:
        result = json.loads(raw)
        if str(result.get("code", "0")) != "0":
            raise RuntimeError(f"Lark 返回异常: {result}")


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": [], "baseline": False, "last_fingerprint": None}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": [], "baseline": False, "last_fingerprint": None}
    if not isinstance(doc, dict):
        return {"sentIds": [], "baseline": False, "last_fingerprint": None}
    ids = doc.get("sentIds")
    return {
        "sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else [],
        "baseline": bool(doc.get("baseline")),
        "last_fingerprint": doc.get("last_fingerprint"),
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
                print(f"[tokenized-treasury-products] json={path}")
                return doc
    raise FileNotFoundError("找不到 tokenized_treasury_products.json")


def yi(usd: float) -> str:
    if abs(usd) >= 1e8:
        return f"{usd / 1e8:.2f} 亿美元"
    return f"{usd / 1e4:.0f} 万美元"


def build_news(latest: dict[str, Any], reasons: list[str]) -> str:
    day = str(latest.get("day") or "")
    total = float(latest.get("total_usd") or 0)
    products = latest.get("products") or []
    top = products[:3] if isinstance(products, list) else []
    bits = []
    for row in top:
        if not isinstance(row, dict):
            continue
        bits.append(
            f"{row.get('symbol')}（约 {yi(float(row.get('market_cap_usd') or 0))}，"
            f"约占样本 {row.get('share_pct')}%）"
        )
    top_bit = "、".join(bits) if bits else "—"
    reason = "；".join(reasons)
    return (
        f"据吴说数据中心统计，截至 {day}，CoinGecko「tokenized-treasuries」样本合计市值约 {yi(total)}，"
        f"产品市值前三为：{top_bit}。{reason}。"
        f"口径为 CoinGecko 分类近似，可能缺部分发行方/产品，合计常低于 Token Terminal 或 rwa.xyz；"
        f"数字可能与外电略有差异。"
        f"\n{PAGE}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    try:
        doc = load_doc()
    except (OSError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    latest = doc["latest"]
    day = str(latest.get("day") or "")
    total = float(latest.get("total_usd") or 0)
    fingerprint = str(latest.get("fingerprint") or "")
    history = [row for row in (doc.get("history") or []) if isinstance(row, dict) and row.get("day")]
    prior = [row for row in history if str(row.get("day")) < day]
    state = load_state()

    if not state.get("baseline") and not args.force:
        state["baseline"] = True
        state["last_fingerprint"] = fingerprint
        save_state(state)
        print(f"[skip] 首日建基线 day={day} total={total/1e8:.2f}亿 top={fingerprint}")
        return 0

    reasons: list[str] = []
    prior_high = max((float(row.get("total_usd") or 0) for row in prior), default=None)
    if prior_high is not None and total > prior_high + 1.0:
        reasons.append("样本合计创本通道新高")
    prev_total = float(prior[-1]["total_usd"]) if prior else None
    dod = None
    if prev_total and prev_total > 0:
        dod = 100.0 * (total - prev_total) / prev_total
        if abs(dod) >= DOD_MIN:
            reasons.append(f"合计较昨日约 {dod:+.1f}%")
    last_fp = state.get("last_fingerprint")
    if fingerprint and last_fp and fingerprint != last_fp:
        reasons.append(f"前三由 {last_fp.replace('|', '/')} 变为 {fingerprint.replace('|', '/')}")
    elif fingerprint and not last_fp:
        state["last_fingerprint"] = fingerprint

    print(f"[tokenized-treasury-products] {day} total={total/1e8:.2f}亿 dod={dod} reasons={reasons}")
    if not reasons and not args.force:
        state["last_fingerprint"] = fingerprint or state.get("last_fingerprint")
        save_state(state)
        print("[skip] 未过线")
        return 0

    msg_id = f"tokenized-treasury-products-{day}"
    text = build_news(latest, reasons or ["快照更新"])
    print("---")
    print(msg_id)
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
    if msg_id in (state.get("sentIds") or []) and not args.force:
        print(f"[skip] already {msg_id}")
        return 0
    try:
        send_lark(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Lark 失败: {exc}", file=sys.stderr)
        return 3
    state["sentIds"] = list(state.get("sentIds") or []) + [msg_id]
    state["baseline"] = True
    state["last_fingerprint"] = fingerprint
    save_state(state)
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"{stamp}\t{msg_id}\tok\n")
    print(f"[ok] pushed {msg_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
