# -*- coding: utf-8 -*-
"""代币化美国国债发行平台份额 → 最新新闻。

公开页面内嵌的汇总表，不需要密钥。只保留「全部 / 平台」这一张。
同一天只发最新一份，不回扫。正文是可直接发稿的新闻。

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
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_PATH = HERE / "output" / "json" / "treasury_platforms.json"
STATE_PATH = HERE / "output" / "treasury_platforms_lark.json"
PAGE_URL = "https://app.rwa.xyz/treasuries"
NEWS_URL = "https://data.wublock123.com/dashboard/treasuries"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
TOP_N = 10


def _ensure_lark_copy() -> None:
    folder = Path(__file__).resolve().parent
    while folder != folder.parent and not (folder / "lark_copy.py").is_file():
        folder = folder.parent
    if str(folder) not in __import__("sys").path:
        __import__("sys").path.insert(0, str(folder))


_ensure_lark_copy()
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


def fetch_rows() -> list[dict[str, Any]]:
    req = urllib.request.Request(PAGE_URL, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise RuntimeError("页面里没有汇总表")
    props = json.loads(match.group(1))["props"]["pageProps"]
    tabs = props.get("leagueTableTabs") or {}
    table = None
    for tab in tabs.get("all") or []:
        if tab.get("key") == "platforms" and not tab.get("loginRequired"):
            table = tab
            break
    rows = ((table or {}).get("data") or {}).get("rows") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        group = row.get("group") or {}
        name = str(group.get("name") or "").strip()
        value = row.get("value")
        if isinstance(value, dict):
            value = value.get("val")
        if not name or not isinstance(value, (int, float)):
            continue
        out.append({
            "name": name,
            "value_usd": round(float(value), 2),
            "share_pct": None,
        })
    total = sum(item["value_usd"] for item in out)
    if total <= 0 or len(out) < 4:
        raise RuntimeError("平台份额不足")
    for item in out:
        item["share_pct"] = round(item["value_usd"] / total * 100, 2)
    out.sort(key=lambda item: -item["value_usd"])
    return out


def save_doc(rows: list[dict[str, Any]]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    doc = {
        "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "day": datetime.now(TZ_CN).strftime("%Y-%m-%d"),
        "total_usd": round(sum(item["value_usd"] for item in rows), 2),
        "rows": rows,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    return doc


def build_news(doc: dict[str, Any]) -> str:
    rows = doc["rows"][:TOP_N]
    bits = [f"{row['name']}（{row['share_pct']:.1f}%）" for row in rows]
    top4 = sum(row["share_pct"] for row in doc["rows"][:4])
    return (
        f"吴说获悉，据吴说数据中心统计，截至 {doc['day']}，代币化美国国债按发行平台计，"
        f"份额居前的为：" + "、".join(bits) + f"。前四家合计约 {top4:.1f}%。\n"
        f"{NEWS_URL}"
    )


def gen_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = gen_sign(secret, ts)
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    if body and str(json.loads(body).get("code", "0")) != "0":
        raise RuntimeError(body)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": []}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": []}
    ids = doc.get("sentIds") if isinstance(doc, dict) else None
    return {"sentIds": [item for item in ids if isinstance(item, str)] if isinstance(ids, list) else []}


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
    args = parser.parse_args()
    try:
        rows = fetch_rows()
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError, OSError, KeyError) as exc:
        print(f"[ERROR] {exc}")
        return 1
    doc = save_doc(rows)
    text = as_news(build_news(doc))
    msg_id = f"treasury-platforms-{doc['day']}"
    print(f"[treasury] day={doc['day']} n={len(rows)} top={rows[0]['name']} {rows[0]['share_pct']}%")
    print("---")
    print(text)
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0
    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK")
        return 2
    state = load_state()
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
        return 0
    try:
        send_lark(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError, OSError) as exc:
        print(f"[ERROR] Lark 失败: {exc}")
        return 1
    print(f"[pushed] {msg_id}")
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-60:]
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
