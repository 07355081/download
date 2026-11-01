# -*- coding: utf-8 -*-
"""Hyperliquid 当日协议收入 → Lark。数据即手续费页的 DefiLlama 日收入。

只使用已经结束的 UTC 日。距今不超过 2 天才发【实时日常报告】。
手续费日环比绝对值达到 65% 另发【历史阈值警报】。更旧的日期不发。
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
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CANDIDATES = [
    HERE / "output" / "json" / "hl_fees_daily.json",
    Path("/root/dashboard/public/json/hyperliquid/hl_fees_daily.json"),
]
STATE_PATH = HERE / "output" / "hl_revenue_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
PAGE = "https://data.wublock123.com/dashboard/hyperliquid"
MAJOR_PCT = 65.0


def _ensure_lark_copy() -> None:
    import sys as _sys
    from pathlib import Path as _Path
    folder = _Path(__file__).resolve().parent
    while folder != folder.parent and not (folder / "lark_copy.py").is_file():
        folder = folder.parent
    if str(folder) not in _sys.path:
        _sys.path.insert(0, str(folder))


_ensure_lark_copy()
from lark_copy import as_news

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


def send(webhook: str, secret: str | None, text: str) -> None:
    text = as_news(text)
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
        body = json.loads(resp.read().decode() or "{}")
    if str(body.get("code", "0")) != "0":
        raise RuntimeError(body)


def series() -> list[dict[str, Any]]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for path in CANDIDATES:
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        data = doc.get("data") if isinstance(doc, dict) else None
        if not isinstance(data, list):
            continue
        rows = []
        for row in data:
            if not isinstance(row, dict):
                continue
            day = str(row.get("day") or "")[:10]
            fees = row.get("fees_usd")
            if len(day) == 10 and day < today and isinstance(fees, (int, float)):
                rows.append(row)
        if rows:
            print(f"[hl-revenue] {path}")
            return sorted(rows, key=lambda row: str(row["day"]))
    raise FileNotFoundError("找不到已结束的 hl_fees_daily")


def yi(value: float) -> str:
    if abs(value) >= 1e8:
        return f"{value / 1e8:.2f} 亿美元"
    return f"{value / 1e4:.2f} 万美元"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    rows = series()
    latest, prev = rows[-1], rows[-2]
    day = str(latest["day"])[:10]
    age = (datetime.now(timezone.utc).date() - datetime.strptime(day, "%Y-%m-%d").date()).days
    fees = float(latest["fees_usd"])
    revenue = latest.get("revenue_usd")
    cumulative = sum(float(row["revenue_usd"]) for row in rows if isinstance(row.get("revenue_usd"), (int, float)))
    pct = (fees - float(prev["fees_usd"])) / abs(float(prev["fees_usd"])) * 100
    revenue_text = f"协议收入约 {yi(float(revenue))}，" if isinstance(revenue, (int, float)) else ""
    print(f"day={day} age={age} fees={fees:.0f} pct={pct:.1f}")
    briefs = []
    if age <= 1:
        briefs.append(
            (
                f"hl-revenue-daily-{day}",
                "【实时日常报告】\n"
                f"据吴说数据中心统计，{day} Hyperliquid 手续费约 {yi(fees)}，{revenue_text}"
                f"日手续费环比 {pct:+.1f}%。已结束日期的协议收入累计约 {yi(cumulative)}。"
                "不含回购金额、全市场永续份额和 Builder Codes。\n"
                f"{PAGE}",
            )
        )
    if abs(pct) >= MAJOR_PCT and age <= 4:
        briefs.append(
            (
                f"hl-revenue-threshold-{day}",
                "【历史阈值警报】\n"
                f"{day} Hyperliquid 手续费约 {yi(fees)}，较前一日 {pct:+.1f}%。"
                "这是历史幅度，不是当天刚出的日常。\n"
                f"{PAGE}",
            )
        )
    if not briefs:
        print("[skip] 没有实时日常，也没有够大的历史变动")
        return 0
    state_path = STATE_PATH
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {"sentIds": []}
    sent = [x for x in state.get("sentIds") or [] if isinstance(x, str)]
    conf = env()
    for msg_id, text in briefs:
        print("---", msg_id)
        print(text)
        if not args.push or msg_id in sent:
            continue
        send(conf["LARK_ALERT_WEBHOOK"], conf.get("LARK_ALERT_SECRET") or None, text)
        sent.append(msg_id)
        print("Lark OK", msg_id)
    if args.push:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state["sentIds"] = sent
        state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
