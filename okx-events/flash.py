#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OKX 事件合约系列 → Lark 新闻体。

只在系列名单变化时发最新一条；不发日常流水，不回扫。
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
    HERE / "output" / "json" / "okx_events.json",
    Path("/root/dashboard/public/json/okx-events/okx_events.json"),
]
STATE_PATH = HERE / "output" / "okx_events_lark.json"
LOG_PATH = HERE / "okx_events_flash.log"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/okx-events"
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
                print(f"[okx-events] json={path}")
                return doc
    raise FileNotFoundError("找不到 okx_events.json")


def build_news(doc: dict[str, Any]) -> str:
    latest = doc["latest"]
    day = str(latest["day"])
    y, m, d = day.split("-")
    stamp = f"{int(y)} 年 {int(m)} 月 {int(d)} 日"
    n = int(latest.get("series_count") or 0)
    live = latest.get("updown_5min_live") if isinstance(latest.get("updown_5min_live"), dict) else {}
    live_names = [name.split("-")[0] for name, count in live.items() if isinstance(count, int) and count > 0]
    if not live_names:
        live_names = ["BTC", "ETH", "SOL"]
    highlights = latest.get("highlights") if isinstance(latest.get("highlights"), list) else []
    titles = []
    for row in highlights[:6]:
        if isinstance(row, dict) and row.get("title"):
            titles.append(str(row["title"]))
    title_bit = f"公开系列示例包括 {('、'.join(titles))}。" if titles else ""
    return (
        f"吴说获悉，据吴说数据中心统计，截至 {stamp}，按 OKX 公开事件合约接口，"
        f"当前可查系列共 {n} 个，其中 {'/'.join(live_names)} 的 5 分钟涨跌合约有 live 市场。"
        f"{title_bit}"
        f"口径为 OKX 公开 API，不是成交额排行；地区可用性以交易所前端为准。"
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
        print(f"[skip] 系列名单未变 series={latest.get('series_count')}")
        return 0
    text = build_news(doc)
    digest = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:12]
    msg_id = f"okx-events-{digest}"
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
