#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""早期融资季度趋势（RootData）→ Lark 新闻体。

只发最新一个已收完自然季，收盘超过 60 天不发（防滞后）；不回扫更早季度。
非 CryptoRank；不写「连续 N 季」除非本序列能算出来。

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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    HERE / "output" / "json" / "financing_early_stage.json",
    Path("/root/dashboard/public/json/financing-early-stage/financing_early_stage.json"),
]
STATE_PATH = HERE / "output" / "financing_early_stage_lark.json"
LOG_PATH = HERE / "flash.log"
JSON_URL = "https://data.wublock123.com/dashboard/financing-news"
PAGE = "https://data.wublock123.com/dashboard/financing-news"
FRESH_DAYS = 60
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


def load_doc() -> dict[str, Any]:
    for path in JSON_CANDIDATES:
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and doc.get("latest_complete_quarter"):
                print(f"[financing-early-stage] json={path}")
                return doc
    raise FileNotFoundError("找不到 financing_early_stage.json")


def quarter_end(q: str) -> date:
    year = int(q[:4])
    qi = int(q[-1])
    month = qi * 3
    if month == 12:
        return date(year, 12, 31)
    nxt = date(year, month + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def yi(usd: float) -> str:
    if abs(usd) >= 1e8:
        return f"{usd / 1e8:.2f} 亿美元"
    return f"{usd / 1e4:.0f} 万美元"


def pct(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.0f}%"


def find_row(doc: dict[str, Any], q: str) -> dict[str, Any] | None:
    for row in doc.get("quarters") or []:
        if isinstance(row, dict) and row.get("quarter") == q:
            return row
    return None


def n_decline_streak(doc: dict[str, Any], latest: str) -> int:
    rows = [r for r in (doc.get("quarters") or []) if isinstance(r, dict)]
    qs = [str(r["quarter"]) for r in rows]
    if latest not in qs:
        return 0
    idx = qs.index(latest)
    streak = 0
    for i in range(idx, 0, -1):
        cur = int(rows[i].get("early_n") or 0)
        prev = int(rows[i - 1].get("early_n") or 0)
        if cur < prev:
            streak += 1
        else:
            break
    return streak


def build_news(doc: dict[str, Any], q: str, row: dict[str, Any]) -> str:
    vs = doc.get("vs_base") if isinstance(doc.get("vs_base"), dict) else {}
    qoq = doc.get("qoq") if isinstance(doc.get("qoq"), dict) else {}
    streak = n_decline_streak(doc, q)
    bits = [
        f"{q} RootData 口径早期融资（Pre-Seed/Seed/Angel/Pre-A 等）约 {int(row['early_n'])} 笔、"
        f"金额约 {yi(float(row['early_usd']))}",
        f"同期后期融资约 {int(row['late_n'])} 笔、金额约 {yi(float(row['late_usd']))}",
    ]
    if qoq.get("prev_quarter"):
        bits.append(
            f"早期较 {qoq['prev_quarter']} 轮次约 {pct(qoq.get('early_n_pct'))}、"
            f"金额约 {pct(qoq.get('early_usd_pct'))}"
        )
    if vs.get("base_quarter"):
        bits.append(
            f"较 {vs['base_quarter']} 轮次约 {pct(vs.get('early_n_pct'))}、"
            f"金额约 {pct(vs.get('early_usd_pct'))}"
        )
    if streak >= 2:
        bits.append(f"早期轮次已连续 {streak} 个季度环比下降")
    text = (
        "吴说获悉，据吴说数据中心统计，"
        + "；".join(bits)
        + "。"
        "口径为 RootData 融资明细按轮次聚合，非 CryptoRank；数字可能与外电略有差异。"
        f"\n{PAGE}"
    )
    return as_news(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--allow-stale", action="store_true")
    args = parser.parse_args()

    try:
        doc = load_doc()
    except (OSError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    q = str(doc.get("latest_complete_quarter") or "")
    if not q:
        print("[skip] 无已收完季度")
        return 0
    row = find_row(doc, q)
    if not row:
        print(f"[skip] 无 {q} 行")
        return 0

    age = (date.today() - quarter_end(q)).days
    if age > FRESH_DAYS and not args.allow_stale:
        print(f"[skip] {q} 已收盘 {age} 天，超过 {FRESH_DAYS} 天")
        return 0

    msg_id = f"financing-early-stage-{q}"
    text = build_news(doc, q, row)
    print(f"[financing-early-stage] q={q} age={age} early_n={row['early_n']}")
    print("---")
    print(f"id={msg_id}")
    print(text)
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0

    state = load_state()
    if msg_id in state["sentIds"] and not args.force:
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
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-80:]
    save_state(state)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} {msg_id} ok=True\n{text}\n\n")
    print(f"[pushed] {msg_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
