#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TradeXYZ（HIP-3 dex=xyz）未平仓 → Lark 新闻体。

口径：Hyperliquid 官方 metaAndAssetCtxs，openInterest × markPx。
只在创本通道已记录持仓历史新高时发最新一条；不回扫旧日，不发日常流水。
非 Token Terminal 专有口径，数字可能与原文略有差异。
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
OUT_PATH = HERE / "output" / "json" / "tradexyz_oi.json"
STATE_PATH = HERE / "output" / "tradexyz_oi_lark.json"
LOG_PATH = HERE / "tradexyz_oi_flash.log"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/hyperliquid"
JSON_URL = "https://data.wublock123.com/dashboard/hyperliquid"
INFO_URL = "https://api.hyperliquid.xyz/info"
DEX = "xyz"
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
    if not STATE_PATH.is_file():
        return {"sentIds": []}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": []}
    ids = doc.get("sentIds") if isinstance(doc, dict) else None
    return {"sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else []}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def info(body: dict[str, Any]) -> Any:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        INFO_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "dashboard-data/tradexyz-oi",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def xyz_oi_usd() -> float:
    payload = info({"type": "metaAndAssetCtxs", "dex": DEX})
    if not isinstance(payload, list) or len(payload) < 2:
        raise RuntimeError("metaAndAssetCtxs 格式异常")
    meta, ctxs = payload[0], payload[1]
    universe = (meta or {}).get("universe") if isinstance(meta, dict) else None
    if not isinstance(universe, list) or not isinstance(ctxs, list):
        raise RuntimeError("universe/ctxs 异常")
    total = 0.0
    for asset, ctx in zip(universe, ctxs):
        if not isinstance(ctx, dict):
            continue
        try:
            total += float(ctx.get("openInterest") or 0) * float(ctx.get("markPx") or 0)
        except (TypeError, ValueError):
            continue
    if total <= 0:
        raise RuntimeError("TradeXYZ 未平仓为 0")
    return total


def yi(usd: float) -> str:
    return f"{usd / 1e8:.2f} 亿美元"


def load_doc() -> dict[str, Any]:
    if not OUT_PATH.is_file():
        return {"source": "hyperliquid info metaAndAssetCtxs dex=xyz", "history": []}
    try:
        doc = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"source": "hyperliquid info metaAndAssetCtxs dex=xyz", "history": []}
    hist = doc.get("history") if isinstance(doc, dict) else None
    return {
        "source": "hyperliquid info metaAndAssetCtxs dex=xyz",
        "history": [row for row in hist if isinstance(row, dict)] if isinstance(hist, list) else [],
    }


def save_doc(history: list[dict[str, Any]], latest: dict[str, Any]) -> None:
    payload = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "hyperliquid info metaAndAssetCtxs dex=xyz",
        "note": "TradeXYZ 未平仓美元=各市场 openInterest×markPx。非 Token Terminal。",
        "latest": latest,
        "history": history[-400:],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)


def build_news(oi: float, prior_high: float | None, day: str) -> str:
    now = datetime.now(TZ_CN)
    stamp = f"{now.year} 年 {now.month} 月 {now.day} 日 {now.hour:02d}:{now.minute:02d}"
    ath_bit = ""
    if prior_high is not None:
        ath_bit = f"高于本通道已记录的最高点 {yi(prior_high)}。"
    return (
        f"据吴说数据中心统计，截至 {stamp}（UTC+8），Hyperliquid HIP-3 提供商 TradeXYZ "
        f"永续未平仓约 {yi(oi)}，创本通道记录新高。{ath_bit}"
        f"口径为 Hyperliquid 官方盘口未平仓乘标记价格（dex=xyz），非 Token Terminal 专有口径；"
        f"数字可能与原文略有差异。"
        f"\n{DASHBOARD_URL}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    try:
        oi = xyz_oi_usd()
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] 拉数失败: {exc}", file=sys.stderr)
        return 1

    day = datetime.now(TZ_CN).strftime("%Y-%m-%d")
    latest = {
        "day": day,
        "asof": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "oi_usd": round(oi, 2),
        "dex": DEX,
    }
    doc = load_doc()
    prior = [
        float(item["oi_usd"])
        for item in doc["history"]
        if str(item.get("day")) != day and isinstance(item.get("oi_usd"), (int, float))
    ]
    prior_high = max(prior) if prior else None
    history = [item for item in doc["history"] if str(item.get("day")) != day]
    history.append(latest)
    history.sort(key=lambda item: str(item.get("day") or ""))
    save_doc(history, latest)

    # 首日只建基线，不推；之后仅在突破已记录最高点时发最新一条
    is_ath = prior_high is not None and oi > prior_high + 1e-6
    print(f"[tradexyz-oi] day={day} oi={oi:.0f} prior_high={prior_high} ath={is_ath}")
    if not is_ath and not args.force:
        print("[skip] 未创持仓历史新高" if prior_high is not None else "[skip] 首日建基线，不推")
        return 0

    msg_id = f"tradexyz-oi-ath-{day}"
    text = as_news(build_news(oi, prior_high, day))
    print("---")
    print(f"id={msg_id}")
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
    state = load_state()
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
        return 0
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
    state["sentIds"] = state["sentIds"][-200:]
    save_state(state)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} {msg_id} ok=True\n{text}\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
