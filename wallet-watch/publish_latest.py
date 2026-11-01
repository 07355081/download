#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""汇总 wallet-watch events → public/json/wallet-watch/latest.json。

供 lark-feeds/wallet-watch 画最近异动柱图与摘要。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "output"
LATEST = OUT_DIR / "latest.json"
DASH_CANDIDATES = [
    Path("/root/dashboard/public/json/wallet-watch/latest.json"),
    HERE.parent.parent / "data-dashboard" / "public" / "json" / "wallet-watch" / "latest.json",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_events() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(OUT_DIR.glob("events*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        case = str(doc.get("case") or path.stem.replace("events_", "") or "ledger")
        for item in doc.get("events") or []:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            row.setdefault("case", case)
            rows.append(row)
    rows.sort(key=lambda r: int(r.get("ts") or 0))
    # dedupe by tx+watch+direction
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for row in reversed(rows):
        key = f"{row.get('tx')}|{row.get('watch')}|{row.get('direction')}|{row.get('amount')}"
        if key in seen:
            continue
        seen.add(key)
        uniq.append(row)
    uniq.reverse()
    return uniq[-120:]


def day_of(ts: int) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def short(addr: str, n: int = 6) -> str:
    s = str(addr or "")
    if len(s) <= n * 2 + 1:
        return s
    return f"{s[:n]}…{s[-n:]}"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    events = load_events()
    # 日度：告警笔数（主序列）
    by_day: dict[str, int] = {}
    for row in events:
        day = day_of(int(row.get("ts") or 0))
        if not day:
            continue
        by_day[day] = by_day.get(day, 0) + 1
    days = [{"day": day, "value": by_day[day], "count": by_day[day]} for day in sorted(by_day)]

    # 最近大额柱：按金额取近 20 条（同资产内可比）
    recent = sorted(events, key=lambda r: float(r.get("amount") or 0), reverse=True)[:20]
    top = []
    for row in recent:
        asset = str(row.get("asset") or "")
        amount = float(row.get("amount") or 0)
        if amount <= 0:
            continue
        label = f"{day_of(int(row.get('ts') or 0))[5:] or '?'} {row.get('direction') or ''} {asset}".strip()
        top.append(
            {
                "name": label,
                "amount": amount,
                "asset": asset,
                "tx": row.get("tx"),
                "chain": row.get("chain"),
            }
        )

    last = events[-1] if events else None
    payload = {
        "updated_at": now_iso(),
        "name": "wallet_watch",
        "source": "wallet-watch events*.json",
        "note": (
            "金额为各资产原生单位（ETH/BTC/USDT 等），不可跨资产直接比，也不折成美元；"
            "折线为近 45 日告警笔数。文末页为平台自有展示。"
        ),
        "count": len(events),
        "day": days[-1]["day"] if days else (day_of(int(last.get("ts") or 0)) if last else None),
        "latest_event": (
            {
                "day": day_of(int(last.get("ts") or 0)),
                "chain": last.get("chain"),
                "direction": last.get("direction"),
                "amount": last.get("amount"),
                "asset": last.get("asset"),
                "watch_short": short(str(last.get("watch") or "")),
                "tx": last.get("tx"),
            }
            if last
            else None
        ),
        "days": days,
        "top": top[:12],
        "events": [
            {
                "day": day_of(int(r.get("ts") or 0)),
                "ts": r.get("ts"),
                "chain": r.get("chain"),
                "watch": r.get("watch"),
                "watch_short": short(str(r.get("watch") or "")),
                "direction": r.get("direction"),
                "amount": r.get("amount"),
                "asset": r.get("asset"),
                "counterpart": r.get("counterpart"),
                "counterpart_short": short(str(r.get("counterpart") or "")),
                "tx": r.get("tx"),
                "case": r.get("case"),
                "alerted_at": r.get("alerted_at"),
            }
            for r in events[-40:]
        ],
    }
    tmp = LATEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(LATEST)
    print(f"[wallet-watch] wrote {LATEST} events={len(events)} days={len(days)}")

    for dst in DASH_CANDIDATES:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(LATEST.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"[wallet-watch] mirrored → {dst}")
        except OSError as exc:
            print(f"[wallet-watch] skip mirror {dst}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
