# -*- coding: utf-8 -*-
"""财库公司账面回报 → Lark。读看板已有的 CoinGecko 持仓成本与当前市值。

只发最新一份快照。文件超过 2 天不发。同一天只发一次。正文是可直接发稿的新闻。
日常稿含：绝对账面浮盈/浮亏领先者 + 回报率前三/后五。未披露成本的公司不纳入绝对盈亏。
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
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/book-return"
ROOT = HERE.parent
JSON_DIRS = [
    Path("/root/dashboard/public/json/crypto-treasuries"),
    HERE / "output" / "json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "crypto-treasuries",
]
# 必须写入 download 源目录，否则 copy_to_dashboard treasuries 镜像会删掉看板里多出来的 book_return.json
OUT_PATH = HERE / "output" / "json" / "book_return.json"
DASH_MIRROR = [
    Path("/root/dashboard/public/json/crypto-treasuries/book_return.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "crypto-treasuries" / "book_return.json",
]
COINS = ("bitcoin", "ethereum", "solana", "binancecoin", "ripple", "hyperliquid")
STATE_PATH = HERE / "output" / "book_return_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
MIN_ENTRY_USD = 50_000_000.0
FRESH_DAYS = 2
TOP_N = 3
BOTTOM_N = 5
LARK_DAILY = "【实时日常报告】"


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
    heads: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s == LARK_DAILY:
            heads.append(s)
        elif not s:
            continue
        else:
            break
    body = as_news(text)
    if heads:
        body = "\n".join(heads) + "\n" + body
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
        body_json = json.loads(resp.read().decode() or "{}")
    if str(body_json.get("code", "0")) != "0":
        raise RuntimeError(body_json)


def json_dir() -> Path:
    for path in JSON_DIRS:
        if (path / "overview_bitcoin.json").is_file():
            return path
    raise FileNotFoundError("找不到 overview_bitcoin.json")


def ticker(symbol: str) -> str:
    text = symbol.upper()
    for suffix in (".US", ".T", ".L", ".HK", ".SS", ".SZ"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def yi(usd: float) -> str:
    return f"{abs(usd) / 1e8:.1f}"


def pct(value: float) -> str:
    return f"{value * 100:+.1f}%"


def zh_date(day: str) -> str:
    year, month, date = day.split("-")
    return f"{int(year)} 年 {int(month)} 月 {int(date)} 日"


def load_rows(folder: Path, now: datetime) -> tuple[str, list[dict[str, Any]]]:
    newest = 0.0
    files: list[tuple[Path, float]] = []
    for coin in COINS:
        path = folder / f"overview_{coin}.json"
        if not path.is_file():
            continue
        mtime = path.stat().st_mtime
        newest = max(newest, mtime)
        files.append((path, mtime))
    if not files:
        raise FileNotFoundError("没有财库总表")
    day = datetime.fromtimestamp(newest, timezone.utc).strftime("%Y-%m-%d")
    age_days = (now - datetime.fromtimestamp(newest, timezone.utc)).total_seconds() / 86400
    if age_days > FRESH_DAYS:
        print(f"[skip] 最新总表 {day} 已过 {age_days:.1f} 天")
        return day, []
    agg: dict[str, dict[str, Any]] = {}
    for path, mtime in files:
        if newest - mtime > FRESH_DAYS * 86400:
            print(f"[skip-file] {path.name} 比最新总表旧")
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            data = payload if isinstance(payload, dict) else {}
        for row in data.get("companies") or []:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").upper()
            name = str(row.get("name") or symbol)
            key = symbol or name
            entry = float(row.get("total_entry_value_usd") or 0)
            current = float(row.get("total_current_value_usd") or 0)
            slot = agg.setdefault(key, {"name": name, "symbol": symbol, "entry": 0.0, "current": 0.0})
            slot["entry"] += entry
            slot["current"] += current
            if name and slot["name"] == slot["symbol"]:
                slot["name"] = name
    rows = []
    for slot in agg.values():
        if slot["entry"] < MIN_ENTRY_USD or slot["current"] <= 0:
            continue
        slot["return"] = slot["current"] / slot["entry"] - 1
        slot["pnl"] = slot["current"] - slot["entry"]
        rows.append(slot)
    rows.sort(key=lambda item: item["return"], reverse=True)
    return day, rows


def company_phrase(row: dict[str, Any]) -> str:
    return f"{row['name']}（{ticker(row['symbol'])}，{pct(row['return'])}）"


def pnl_phrase(row: dict[str, Any]) -> str:
    side = "浮盈" if float(row["pnl"]) >= 0 else "浮亏"
    return (
        f"{row['name']}（{ticker(row['symbol'])}）账面{side}约 {yi(float(row['pnl']))} 亿美元"
        f"（回报率约 {pct(float(row['return']))}）"
    )


def mstr_sentence(rows: list[dict[str, Any]]) -> str:
    row = next((item for item in rows if ticker(item["symbol"]) == "MSTR"), None)
    if row is None:
        return ""
    pnl = float(row["pnl"])
    side = "浮盈" if pnl >= 0 else "浮亏"
    return (
        f"Strategy（MSTR）持仓成本约 {yi(row['entry'])} 亿美元，"
        f"当前价值约 {yi(row['current'])} 亿美元，"
        f"账面{side}约 {yi(pnl)} 亿美元，回报率约 {pct(row['return'])}。"
    )


def absolute_pnl_sentence(rows: list[dict[str, Any]]) -> str:
    by_pnl = sorted(rows, key=lambda item: float(item["pnl"]), reverse=True)
    best = by_pnl[0]
    bits = [f"按持仓成本计账面浮盈最高的为 {pnl_phrase(best)}"]
    losers = [item for item in by_pnl if float(item["pnl"]) < 0]
    if losers:
        bits.append(f"账面浮亏最大的为 {pnl_phrase(losers[-1])}")
    return "，".join(bits) + "。"


def write_json(day: str, rows: list[dict[str, Any]]) -> Path:
    by_pnl = sorted(rows, key=lambda item: float(item["pnl"]), reverse=True)
    payload = {
        "day": day,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "CoinGecko 持仓成本 vs 市值；未披露成本（entry=0）的公司不纳入。",
        "top_pnl": {
            "symbol": ticker(by_pnl[0]["symbol"]),
            "name": by_pnl[0]["name"],
            "pnl_usd": round(float(by_pnl[0]["pnl"]), 2),
            "return_pct": round(float(by_pnl[0]["return"]) * 100, 1),
        }
        if by_pnl
        else None,
        "bottom_pnl": {
            "symbol": ticker(by_pnl[-1]["symbol"]),
            "name": by_pnl[-1]["name"],
            "pnl_usd": round(float(by_pnl[-1]["pnl"]), 2),
            "return_pct": round(float(by_pnl[-1]["return"]) * 100, 1),
        }
        if by_pnl and float(by_pnl[-1]["pnl"]) < 0
        else None,
        "returns": [
            {
                "symbol": ticker(row["symbol"]),
                "name": row["name"],
                "return_pct": round(float(row["return"]) * 100, 1),
                "pnl_usd": round(float(row["pnl"]), 2),
                "entry_usd": round(float(row["entry"]), 2),
                "current_usd": round(float(row["current"]), 2),
            }
            for row in rows
        ],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(OUT_PATH)
    for mirror in DASH_MIRROR:
        try:
            mirror.parent.mkdir(parents=True, exist_ok=True)
            mtmp = mirror.with_suffix(".json.tmp")
            mtmp.write_text(text, encoding="utf-8")
            mtmp.replace(mirror)
        except OSError:
            continue
    return OUT_PATH


def build_news(day: str, rows: list[dict[str, Any]]) -> str:
    if len(rows) < TOP_N + BOTTOM_N:
        raise RuntimeError(f"可排名公司只有 {len(rows)} 家")
    top = "、".join(company_phrase(row) for row in rows[:TOP_N])
    bottom_rows = list(reversed(rows[-BOTTOM_N:]))
    bottom = "、".join(company_phrase(row) for row in bottom_rows)
    return (
        f"{LARK_DAILY}\n"
        f"吴说获悉，据吴说数据中心统计，截至 {zh_date(day)}，"
        f"{absolute_pnl_sentence(rows)}"
        f"按回报率计，表现前三为 {top}。"
        f"{mstr_sentence(rows)}"
        f"账面回报率较低的五家为 {bottom}。"
        f"口径为 CoinGecko 公开财库持仓成本与当前市值，未披露成本的公司不纳入绝对盈亏排名；非 Artemis。\n"
        f"{PAGE}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--history-scan", action="store_true")
    args = parser.parse_args()
    args.history_scan = False
    folder = json_dir()
    day, rows = load_rows(folder, datetime.now(timezone.utc))
    if not rows:
        return 0
    written = write_json(day, rows)
    print(f"[book-return] wrote {written} n={len(rows)}")
    msg_id = f"dat-book-return-{day}"
    text = build_news(day, rows)
    print(msg_id)
    print(text)
    if not args.push:
        return 0
    state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.is_file() else {}
    sent = [item for item in state.get("sentIds", []) if isinstance(item, str)]
    if msg_id in sent:
        print("[skip] already", msg_id)
        return 0
    conf = env()
    send(conf["LARK_ALERT_WEBHOOK"], conf.get("LARK_ALERT_SECRET") or None, text)
    sent.append(msg_id)
    state["sentIds"] = sent[-30:]
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Lark OK", msg_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
