#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TON 交易机器人日成交/交易者（只读 Dune xRocket 缓存，不 execute）。

查询 5350885：Daily Volume / Unique Traders / Txs（dune.com/xrocket_tg/trading-bots-on-ton）
TON 美元价：CoinGecko 免费，仅用于换算展示。
"""
from __future__ import annotations

import json
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "output" / "json" / "ton_trading_bots.json"
Q_DAILY = 5350885
MAX_CACHE_AGE_H = 72.0
TON_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=usd"
ENV_FILES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]


def api_key() -> str:
    for path in ENV_FILES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("DUNE_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("未配置 DUNE_API_KEY")


def get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    req = urllib.request.Request(
        url,
        headers=headers or {"User-Agent": "dashboard-ton-bots", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_ended(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    if "." in text:
        head, _, tail = text.partition(".")
        frac, sign, offset = tail.partition("+")
        text = f"{head}.{frac[:6]}{sign}{offset}" if sign else f"{head}.{frac[:6]}"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fetch_rows(query_id: int, key: str) -> tuple[list[dict[str, Any]], str | None]:
    url = f"https://api.dune.com/api/v1/query/{query_id}/results?limit=1000"
    rows: list[dict[str, Any]] = []
    ended: str | None = None
    while url:
        payload = get_json(url, {"X-Dune-API-Key": key})
        rows.extend((payload.get("result") or {}).get("rows") or [])
        ended = payload.get("execution_ended_at") or ended
        url = payload.get("next_uri")
        if len(rows) > 20000:
            break
    return rows, ended


def ton_usd() -> float | None:
    try:
        doc = get_json(TON_PRICE_URL)
        val = ((doc.get("the-open-network") or {}).get("usd"))
        return float(val) if isinstance(val, (int, float)) else None
    except Exception as exc:
        print(f"[ton-bots] price skip: {exc}")
        return None


def main() -> int:
    key = api_key()
    rows, ended = fetch_rows(Q_DAILY, key)
    if not rows:
        raise SystemExit("ton bots 缓存为空")

    by_day: dict[str, dict[str, float]] = defaultdict(lambda: {"vol": 0.0, "traders": 0.0, "txs": 0.0})
    bot_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        day = str(row.get("date") or "")[:10]
        if len(day) != 10:
            continue
        vol = float(row.get("volume_ton") or 0)
        traders = float(row.get("daily_unique_traders") or 0)
        txs = float(row.get("daily_transactions") or 0)
        by_day[day]["vol"] += vol
        by_day[day]["traders"] += traders
        by_day[day]["txs"] += txs
        bot_day[day].append(
            {
                "bot": row.get("bot_name"),
                "volume_ton": round(vol, 4),
                "traders": int(traders),
                "txs": int(txs),
            }
        )

    days = sorted(by_day)
    latest = days[-1]
    prev = days[-2] if len(days) >= 2 else None
    cur = by_day[latest]
    prev_vol = by_day[prev]["vol"] if prev else None
    dod = None
    if prev_vol and prev_vol > 0:
        dod = round((cur["vol"] / prev_vol - 1.0) * 100.0, 2)

    price = ton_usd()
    top = sorted(bot_day[latest], key=lambda x: float(x["volume_ton"]), reverse=True)[:5]
    age = None
    dt = parse_ended(ended)
    if dt:
        age = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
    # 有执行时间戳才按小时判旧；无戳则看最新业务日是否过近 3 天
    if age is not None:
        stale = age > MAX_CACHE_AGE_H
    else:
        try:
            lag = (datetime.now(timezone.utc).date() - datetime.strptime(latest, "%Y-%m-%d").date()).days
        except ValueError:
            lag = 99
        stale = lag > 3

    series = [
        {
            "day": d,
            "volume_ton": round(by_day[d]["vol"], 4),
            "traders_sum": int(by_day[d]["traders"]),
            "txs": int(by_day[d]["txs"]),
        }
        for d in days[-90:]
    ]

    doc = {
        "source": "dune.com/xrocket_tg/trading-bots-on-ton (cache only)",
        "query_id": Q_DAILY,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "execution_ended_at": ended,
        "cache_age_hours": age,
        "stale": stale,
        "day": latest,
        "prev_day": prev,
        "volume_ton": round(cur["vol"], 4),
        "volume_usd": round(cur["vol"] * price, 2) if price else None,
        "ton_usd": price,
        "traders_sum": int(cur["traders"]),
        "txs": int(cur["txs"]),
        "dod_pct": dod,
        "top_bots": top,
        "series": series,
        "note": "交易者为各机器人当日 unique 加总，可能跨 bot 重复；成交为 TON 计价。",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[ton-trading-bots] day={latest} vol={cur['vol']:.1f} TON "
        f"traders={doc['traders_sum']} dod={dod} stale={stale}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
