#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ETH 质押总量 / 占供应 / 入口队列（validatorqueue.com 公开页）。

从页面内嵌 historical_data 解析日序列，不占 CoinGlass / Glassnode / Dune。
输出：output/json/eth_staking.json
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "eth_staking.json"
PAGE_URL = "https://www.validatorqueue.com/"
UA = "dashboard-data/eth-staking"
TIMEOUT = 60
ATTEMPTS = 4

# historical_data = [ {...}, ... ];
DATA_RE = re.compile(
    r"\b(?:var\s+|let\s+|const\s+)?historical_data\s*=\s*(\[[\s\S]*?\])\s*;",
    re.I,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_html() -> str:
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(
                PAGE_URL,
                headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"},
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < ATTEMPTS:
                time.sleep(min(2**attempt, 12))
                continue
            raise
    raise RuntimeError(f"GET failed: {last}")


def parse_rows(html: str) -> list[dict[str, Any]]:
    match = DATA_RE.search(html)
    if not match:
        raise RuntimeError("页面里没有 historical_data")
    raw = json.loads(match.group(1))
    if not isinstance(raw, list) or not raw:
        raise RuntimeError("historical_data 为空")
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        day = str(item.get("date") or "")[:10]
        if len(day) != 10 or day[4] != "-":
            continue
        staked = item.get("staked_amount")
        if not isinstance(staked, (int, float)) or float(staked) <= 0:
            continue
        pct = item.get("staked_percent")
        entry = item.get("entry_queue")
        exit_q = item.get("exit_queue")
        validators = item.get("validators")
        row: dict[str, Any] = {
            "day": day,
            "staked_eth": float(staked),
            "staked_pct": float(pct) if isinstance(pct, (int, float)) else None,
            "entry_queue_eth": float(entry) if isinstance(entry, (int, float)) else None,
            "exit_queue_eth": float(exit_q) if isinstance(exit_q, (int, float)) else None,
            "validators": float(validators) if isinstance(validators, (int, float)) else None,
            "apr_pct": float(item["apr"]) if isinstance(item.get("apr"), (int, float)) else None,
        }
        out.append(row)
    by_day = {r["day"]: r for r in out}
    return [by_day[d] for d in sorted(by_day)]


def main() -> int:
    html = fetch_html()
    rows = parse_rows(html)
    if len(rows) < 2:
        raise SystemExit("日序列不足 2 天")
    last = rows[-1]
    payload = {
        "updated_at": now_iso(),
        "name": "eth_staking",
        "source": "validatorqueue.com historical_data",
        "source_url": PAGE_URL,
        "note": (
            "质押总量与入口队列来自 validatorqueue.com 公开页内嵌日序列。"
            "与 Cointelegraph / beaconcha.in 可能不完全一致。"
        ),
        "day": last["day"],
        "latest": last,
        "data": rows,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(
        f"[eth-staking] wrote {OUT_PATH} days={len(rows)} "
        f"as_of={last['day']} staked={last['staked_eth']:.0f} "
        f"pct={last.get('staked_pct')} entry={last.get('entry_queue_eth')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
