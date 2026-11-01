# -*- coding: utf-8 -*-
"""HYPE Assistance Fund 回购余额 → Lark。数据即 /dashboard/burns/hype。

口径：官方 Assistance Fund 的 HYPE 余额快照；日增量=余额较上一快照增加。
与 Onchain Lens「回购并销毁」叙述接近，但不是其同源。

只发最新快照日，不回扫历史。
- 日常：快照日距今 ≤1 天 →【实时日常报告】+ 可发稿新闻
- 阈值：日增量相对累计 ≥0.05% 且日增量 ≥1 万枚，或累计较上一快照 |Δ|≥5% →【历史阈值警报】

用法：
  python hype_flash.py
  python hype_flash.py --push
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
    HERE / "output" / "json" / "hype.json",
    Path("/root/dashboard/public/json/token-burns/hype.json"),
]
STATE_PATH = HERE / "output" / "hype_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
PAGE = "https://data.wublock123.com/dashboard/burns/hype"
MAX_SUPPLY = 1_000_000_000
SHIFT_PCT = 5.0
DAILY_MIN_TOKENS = 10_000.0
DAILY_MIN_OF_CUM_PCT = 0.05  # 日增量占累计 ≥0.05%
LARK_DAILY = "【实时日常报告】"
LARK_THRESHOLD = "【历史阈值警报】"


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
    body = as_news(text)
    for mark in (LARK_THRESHOLD, LARK_DAILY):
        if text.startswith(mark):
            body = f"{mark}\n{body}"
            break
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": body}}
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


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": []}
    doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    ids = doc.get("sentIds") if isinstance(doc, dict) else None
    return {"sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else []}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_doc() -> dict[str, Any]:
    for path in CANDIDATES:
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        data = doc.get("data") if isinstance(doc, dict) else None
        if isinstance(data, list) and data:
            print(f"[hype] {path}")
            return doc
    raise FileNotFoundError("找不到 hype.json")


def cn_day(day: str) -> str:
    y, m, d = day.split("-")
    return f"{int(y)} 年 {int(m)} 月 {int(d)} 日"


def fmt_tokens(n: float) -> str:
    if abs(n) >= 10_000:
        return f"{n / 10_000:.2f} 万枚"
    return f"{n:,.0f} 枚"


def fmt_usd(n: float) -> str:
    if abs(n) >= 1e8:
        return f"{n / 1e8:.2f} 亿美元"
    if abs(n) >= 1e4:
        return f"{n / 1e4:.2f} 万美元"
    return f"{n:,.0f} 美元"


def build_news(doc: dict[str, Any], latest: dict[str, Any], prev: dict[str, Any] | None) -> str:
    day = str(latest["day"])[:10]
    cum = float(latest["cumulative_burned"])
    max_supply = float(doc.get("max_supply") or MAX_SUPPLY)
    price = latest.get("price_usd")
    if not isinstance(price, (int, float)):
        price = doc.get("latest_price_usd")
    price_f = float(price) if isinstance(price, (int, float)) else None

    day_tokens = latest.get("burned_tokens")
    day_usd = latest.get("burned_usd")
    if not isinstance(day_tokens, (int, float)) and prev and isinstance(prev.get("cumulative_burned"), (int, float)):
        day_tokens = cum - float(prev["cumulative_burned"])
    if (
        not isinstance(day_usd, (int, float))
        and isinstance(day_tokens, (int, float))
        and price_f is not None
    ):
        day_usd = float(day_tokens) * price_f

    bits: list[str] = []
    if isinstance(day_tokens, (int, float)) and float(day_tokens) > 0:
        piece = f"Assistance Fund 较上一快照增加约 {fmt_tokens(float(day_tokens))} HYPE"
        if isinstance(day_usd, (int, float)) and float(day_usd) > 0:
            piece += f"，约合 {fmt_usd(float(day_usd))}"
            if float(day_tokens) > 0:
                avg = float(day_usd) / float(day_tokens)
                piece += f"，折合均价约 {avg:.2f} 美元"
        bits.append(piece)
    bits.append(f"累计余额约 {fmt_tokens(cum)} HYPE")
    if max_supply > 0:
        bits.append(f"约占最大供应量 {cum / max_supply * 100:.2f}%")
    if price_f is not None and price_f > 0:
        bits.append(f"按现价约合 {fmt_usd(cum * price_f)}")
    body = "，".join(bits) + "。"
    return (
        f"据吴说数据中心统计，截至 {cn_day(day)}，{body}"
        "口径为 Hyperliquid Assistance Fund 链上余额快照及日增量，非 Onchain Lens。"
        f"\n\n{PAGE}"
    )


def should_threshold(latest: dict[str, Any], prev: dict[str, Any] | None, cum: float) -> bool:
    if prev and float(prev.get("cumulative_burned") or 0):
        pct = (cum - float(prev["cumulative_burned"])) / abs(float(prev["cumulative_burned"])) * 100
        if abs(pct) >= SHIFT_PCT:
            return True
    day_tokens = latest.get("burned_tokens")
    if isinstance(day_tokens, (int, float)) and float(day_tokens) >= DAILY_MIN_TOKENS and cum > 0:
        if float(day_tokens) / cum * 100 >= DAILY_MIN_OF_CUM_PCT:
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    doc = load_doc()
    series = sorted(
        [row for row in doc["data"] if isinstance(row, dict) and row.get("cumulative_burned") is not None],
        key=lambda row: str(row.get("day")),
    )
    latest = series[-1]
    prev = series[-2] if len(series) > 1 else None
    day = str(latest["day"])[:10]
    cum = float(latest["cumulative_burned"])
    age = (datetime.now(timezone.utc).date() - datetime.strptime(day, "%Y-%m-%d").date()).days
    news = build_news(doc, latest, prev)
    print(f"day={day} age={age} cum={cum:.0f}")

    briefs: list[tuple[str, str]] = []
    if age <= 1:
        briefs.append((f"hype-fund-daily-{day}", f"{LARK_DAILY}\n{news}"))
    if should_threshold(latest, prev, cum) and age <= 7:
        briefs.append((f"hype-fund-threshold-{day}", f"{LARK_THRESHOLD}\n{news}"))
    if not briefs:
        print("[skip] 无实时日常，也无过线阈值")
        return 0

    state = load_state()
    conf = env()
    for msg_id, text in briefs:
        print("---", msg_id, "already" if msg_id in state["sentIds"] else "new")
        print(text)
        if not args.push:
            continue
        if msg_id in state["sentIds"] and not args.force:
            continue
        send(conf["LARK_ALERT_WEBHOOK"], conf.get("LARK_ALERT_SECRET") or None, text)
        if msg_id not in state["sentIds"]:
            state["sentIds"].append(msg_id)
        print("Lark OK", msg_id)
        time.sleep(0.3)
    if args.push:
        state["sentIds"] = state["sentIds"][-160:]
        save_state(state)
    elif briefs:
        print("\n[dry-run] 未推送")
    return 0


if __name__ == "__main__":
    sys.exit(main())
