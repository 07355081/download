"""固定名单的美股盘中涨跌，覆写 tradfi-session-moves.json。"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import _paths as P
from yahoo import fetch_session_quotes

# 稿子顺序。name 用在「名称（代码）」里。
SESSION_BASKET: list[dict[str, str]] = [
    {"ticker": "MU", "name_zh": "美光科技", "group": "ai"},
    {"ticker": "AMD", "name_zh": "AMD", "group": "ai"},
    {"ticker": "TSM", "name_zh": "台积电", "group": "ai"},
    {"ticker": "NVDA", "name_zh": "英伟达", "group": "ai"},
    {"ticker": "AAOI", "name_zh": "应用光电", "group": "ai"},
    {"ticker": "MRVL", "name_zh": "迈威尔科技", "group": "ai"},
    {"ticker": "SKHY", "name_zh": "SK 海力士", "group": "ai"},
    {"ticker": "AMSC", "name_zh": "美国超导", "group": "ai"},
    {"ticker": "SNDK", "name_zh": "闪迪", "group": "ai"},
    {"ticker": "AVGO", "name_zh": "博通", "group": "ai"},
    {"ticker": "ORCL", "name_zh": "甲骨文", "group": "ai"},
    {"ticker": "CRCL", "name_zh": "Circle", "group": "crypto"},
    {"ticker": "MSTR", "name_zh": "Strategy", "group": "crypto"},
    {"ticker": "COIN", "name_zh": "Coinbase", "group": "crypto"},
    {"ticker": "BTDR", "name_zh": "Bitdeer", "group": "crypto"},
]

MOVES_PATH = P.OUTPUT / "tradfi-session-moves.json"
HISTORY_DIR = P.OUTPUT / "tradfi-session-moves-history"
_NY = ZoneInfo("America/New_York")


def session_date(asof_ms: int) -> str:
    return datetime.fromtimestamp(asof_ms / 1000, tz=_NY).date().isoformat()


def build_session_moves(quotes: dict[str, dict], *, now_ms: int | None = None) -> dict[str, Any]:
    """quotes 缺任何一只，或不是同一个常规交易日，complete 为 false。"""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    rows: list[dict[str, Any]] = []
    dates: list[str] = []
    for spec in SESSION_BASKET:
        quote = quotes.get(spec["ticker"])
        if not quote or not quote.get("regular"):
            continue
        change = quote.get("change_pct")
        asof = quote.get("asof_ms")
        last = quote.get("last")
        if not isinstance(change, (int, float)) or not isinstance(asof, int) or not isinstance(last, (int, float)):
            continue
        if change != change:
            continue
        dates.append(session_date(asof))
        rows.append({
            "ticker": spec["ticker"],
            "name_zh": spec["name_zh"],
            "group": spec["group"],
            "change_pct": change,
            "last": last,
            "asof_ms": asof,
        })
    same_day = len(dates) == len(SESSION_BASKET) and len(set(dates)) == 1
    tickers = [row["ticker"] for row in rows]
    wanted = [spec["ticker"] for spec in SESSION_BASKET]
    complete = same_day and tickers == wanted
    return {
        "asof_ms": now_ms,
        "session_date": dates[0] if complete else None,
        "complete": complete,
        "rows": rows if complete else [],
    }


def write_session_moves(http) -> bool:
    quotes = fetch_session_quotes(http, [spec["ticker"] for spec in SESSION_BASKET])
    payload = build_session_moves(quotes)
    P.atomic_write_json(MOVES_PATH, payload)
    # Archive each complete session day for threshold history backfill.
    session = payload.get("session_date")
    if payload.get("complete") and isinstance(session, str) and len(session) == 10:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        hist_path = HISTORY_DIR / f"{session}.json"
        P.atomic_write_json(hist_path, payload)
        print(f"archived {hist_path}", flush=True)
    print(
        f"wrote {MOVES_PATH} complete={payload['complete']} session={payload['session_date']} "
        f"quotes={len(quotes)}/{len(SESSION_BASKET)}",
        flush=True,
    )
    return bool(payload["complete"])
