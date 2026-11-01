#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hyperliquid HIP-3 可交易市场数 → Lark 新闻。

名单变化才发最新一条；首日建基线会发。不回扫。
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
    HERE / "output" / "json" / "hl_hip3_markets.json",
    Path("/root/dashboard/public/json/hl-hip3-markets/hl_hip3_markets.json"),
]
STATE_PATH = HERE / "output" / "hl_hip3_markets_lark.json"
LOG_PATH = HERE / "hl_hip3_markets_flash.log"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/hl-hip3-markets"
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
        return {"sentIds": [], "lastFingerprint": ""}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": [], "lastFingerprint": ""}
    ids = doc.get("sentIds") if isinstance(doc, dict) else None
    return {
        "sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else [],
        "lastFingerprint": str(doc.get("lastFingerprint") or "") if isinstance(doc, dict) else "",
    }


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def append_log(stamp: str, msg_id: str, ok: bool, text: str) -> None:
    line = json.dumps({"ts": stamp, "id": msg_id, "ok": ok, "text": text}, ensure_ascii=False)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def load_doc() -> dict[str, Any]:
    for path in JSON_CANDIDATES:
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("latest"), dict):
                print(f"[hl-hip3-markets] json={path}")
                return doc
    raise FileNotFoundError("找不到 hl_hip3_markets.json")


def build_news(doc: dict[str, Any]) -> str:
    latest = doc["latest"]
    day = str(latest["day"])
    y, m, d = day.split("-")
    stamp = f"{int(y)} 年 {int(m)} 月 {int(d)} 日"
    n = int(latest.get("series_count") or 0)
    unique = int(latest.get("unique_count") or 0)
    builders = latest.get("top_builders") if isinstance(latest.get("top_builders"), list) else []
    bits = []
    for row in builders[:4]:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        count = row.get("markets")
        if name and isinstance(count, int):
            bits.append(f"{name} {count} 个")
    builder_bit = f"靠前的是 {'、'.join(bits)}。" if bits else ""
    return (
        f"吴说获悉，据吴说数据中心统计，截至 {stamp}，"
        f"Hyperliquid HIP-3 可交易永续市场共 {n} 个，去重标的约 {unique} 个。"
        f"{builder_bit}"
        f"口径为 Hyperliquid 官方接口的非 Core 市场，不是全行业 Perp DEX，也不是 Blockworks 的标的统计。"
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
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1
    latest = doc["latest"]
    fingerprint = str(latest.get("fingerprint") or "")
    if not fingerprint:
        print("[ERROR] 缺少 fingerprint", file=sys.stderr)
        return 1
    state = load_state()
    if fingerprint == state.get("lastFingerprint") and not args.force:
        print(f"[skip] 市场名单未变 markets={latest.get('series_count')}")
        return 0
    text = build_news(doc)
    digest = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:12]
    msg_id = f"hl-hip3-markets-{digest}"
    print("---")
    print(f"id={msg_id}")
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
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
        state["lastFingerprint"] = fingerprint
        save_state(state)
        return 0
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    try:
        send_lark(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Lark 失败: {exc}", file=sys.stderr)
        append_log(stamp, msg_id, False, text)
        return 1
    print(f"[pushed] {msg_id}")
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-60:]
    state["lastFingerprint"] = fingerprint
    save_state(state)
    append_log(stamp, msg_id, True, text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
