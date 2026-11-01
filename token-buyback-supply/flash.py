# -*- coding: utf-8 -*-
"""持有人收入折合代币超过流通增量 → 一条可发稿新闻。

只发最新窗口。名单没变不重复发。数据停在两天前则不发。不回扫旧窗口。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    HERE / "output" / "json" / "ytd.json",
    Path("/root/dashboard/public/json/token-buyback-supply/ytd.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "token-buyback-supply" / "ytd.json",
]
STATE_PATH = HERE / "output" / "lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
PAGE = "https://data.wublock123.com/dashboard/burns"


def _ensure_lark_copy() -> None:
    folder = Path(__file__).resolve().parent
    while folder != folder.parent and not (folder / "lark_copy.py").is_file():
        folder = folder.parent
    if str(folder) not in __import__("sys").path:
        __import__("sys").path.insert(0, str(folder))


_ensure_lark_copy()
from lark_copy import as_news  # noqa: E402


def env() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            out[key] = os.environ[key]
    return out


def load_doc() -> dict[str, Any]:
    for path in JSON_CANDIDATES:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError("ytd.json missing")


def wan_tokens(value: float) -> str:
    abs_value = abs(value)
    if abs_value >= 1e8:
        return f"{value / 1e8:.2f} 亿枚"
    if abs_value >= 1e4:
        return f"{value / 1e4:.2f} 万枚"
    return f"{value:.0f} 枚"


def usd_yi(value: float) -> str:
    return f"{value / 1e8:.2f} 亿美元"


def fresh(end: str) -> bool:
    try:
        day = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - day <= timedelta(days=3)


def news(doc: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    start = doc.get("window_start")
    end = doc.get("window_end")
    bits = []
    for row in rows:
        change = row.get("supply_change_pct")
        change_txt = "流通量变化缺少对照" if change is None else f"同期流通量变化 {change:+.1f}%"
        bits.append(
            f"{row.get('name')}（{row.get('symbol')}）持有人收入约 {usd_yi(float(row.get('holders_revenue_usd') or 0))}，"
            f"按当日收盘价折合约 {wan_tokens(float(row.get('estimated_tokens') or 0))}，{change_txt}"
        )
    body = (
        f"据吴说数据中心统计，{start} 至 {end}，"
        + "；".join(bits)
        + "。上述折合代币数量超过同期流通量增加。"
        f"口径为 DefiLlama 持有人收入，不是代币回购台账。详见：{PAGE}"
    )
    return as_news(body)


def send(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(
            hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
        ).decode()
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        result = json.loads(resp.read().decode() or "{}")
    if str(result.get("code", "0")) != "0":
        raise RuntimeError(result)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    doc = load_doc()
    end = str(doc.get("window_end") or "")
    rows = [row for row in doc.get("rows") or [] if isinstance(row, dict) and row.get("exceeds_supply_growth")]
    rows.sort(key=lambda row: str(row.get("symbol")))
    if not rows or not fresh(end):
        print(f"skip rows={len(rows)} end={end}")
        return
    symbols = ",".join(str(row.get("symbol")) for row in rows)
    state = {"sent": ""}
    if STATE_PATH.is_file():
        loaded = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and isinstance(loaded.get("sent"), str):
            state = loaded
    if state.get("sent") == symbols:
        print(f"already sent {symbols}")
        return
    text = news(doc, rows)
    print(text)
    if not args.push:
        return
    creds = env()
    webhook = creds.get("LARK_ALERT_WEBHOOK", "").strip()
    if not webhook:
        raise SystemExit("LARK_ALERT_WEBHOOK missing")
    send(webhook, creds.get("LARK_ALERT_SECRET"), text)
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps({"sent": symbols, "end": end, "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print("pushed", symbols)


if __name__ == "__main__":
    main()
