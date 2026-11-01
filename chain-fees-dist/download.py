#!/usr/bin/env python3
"""公链 24h 手续费长尾分档（DefiLlama）→ 落盘。

从 overview/fees 筛 protocolType=chain，统计：
  宇宙条数、<1k、<10、=0、≥10 万美元，以及 ≥10 万美元名单。

输出：
  output/json/chain_fees_dist_latest.json
  output/json/chain_fees_dist_history/YYYY-MM-DD.json

用法：
  python download.py
  python download.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
JSON_DIR = HERE / "output" / "json"
LATEST_PATH = JSON_DIR / "chain_fees_dist_latest.json"
HISTORY_DIR = JSON_DIR / "chain_fees_dist_history"

OVERVIEW_URL = (
    "https://api.llama.fi/overview/fees"
    "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyFees"
)
SOURCE_URL = "https://defillama.com/chains"
ATTEMPTS = 4
TIMEOUT = 90
TZ_CN = timezone(timedelta(hours=8))
TOP_MIN_USD = 100_000.0


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def shanghai_day() -> str:
    return datetime.now(TZ_CN).date().isoformat()


def http_get_json(url: str) -> dict:
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "dashboard-data/chain-fees-dist"}
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < ATTEMPTS:
                wait = min(2**attempt, 15)
                print(f"    [warn] attempt {attempt}/{ATTEMPTS}: {exc}; retry in {wait}s")
                time.sleep(wait)
    raise RuntimeError(f"GET {url} failed after {ATTEMPTS} attempts: {last}")


def is_chain_row(row: dict[str, Any]) -> bool:
    if row.get("protocolType") == "chain":
        return True
    if row.get("category") == "Chain":
        return True
    did = str(row.get("defillamaId") or "")
    return did.startswith("chain#")


def build_snapshot(doc: dict[str, Any], *, day: str) -> dict[str, Any]:
    protocols = doc.get("protocols") or []
    rows: list[dict[str, Any]] = []
    for item in protocols:
        if not isinstance(item, dict) or not is_chain_row(item):
            continue
        try:
            fees = float(item.get("total24h") or 0)
        except (TypeError, ValueError):
            continue
        if fees != fees:  # NaN
            continue
        name = str(item.get("displayName") or item.get("name") or "").strip()
        if not name:
            continue
        rows.append({"name": name, "fees_usd": fees, "slug": str(item.get("slug") or "")})

    rows.sort(key=lambda r: r["fees_usd"], reverse=True)
    universe = len(rows)
    lt_1k = sum(1 for r in rows if r["fees_usd"] < 1_000)
    lt_10 = sum(1 for r in rows if r["fees_usd"] < 10)
    eq_0 = sum(1 for r in rows if r["fees_usd"] == 0)
    ge_100k_rows = [r for r in rows if r["fees_usd"] >= TOP_MIN_USD]
    top = [
        {"name": r["name"], "fees_usd": round(r["fees_usd"], 2), "slug": r["slug"]}
        for r in ge_100k_rows
    ]
    return {
        "code": "0",
        "msg": "success",
        "name": "chain_fees_dist",
        "description": "公链 24h 手续费长尾分档(DefiLlama overview/fees · Chain)",
        "source": "defillama",
        "source_url": SOURCE_URL,
        "methodology": {
            "universe": "protocolType=chain (or category=Chain) entries in overview/fees",
            "window": "rolling 24h total24h at fetch time",
            "note": "Chain count drifts with DefiLlama adapters; not a fixed 558.",
        },
        "updated_at": now_utc_iso(),
        "day": day,
        "universe": universe,
        "lt_1k": lt_1k,
        "lt_10": lt_10,
        "eq_0": eq_0,
        "ge_100k": len(top),
        "top": top,
        "all_chains_hint": len(doc.get("allChains") or []),
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", default=str(LATEST_PATH))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("[chain-fees-dist] DefiLlama overview/fees · Chain buckets")
    if args.dry_run:
        print("DRY-RUN: 将请求 overview/fees，不写盘")
        return 0

    started = time.time()
    doc = http_get_json(OVERVIEW_URL)
    day = shanghai_day()
    payload = build_snapshot(doc, day=day)
    dest = Path(args.out)
    write_json(dest, payload)
    hist = HISTORY_DIR / f"{day}.json"
    write_json(hist, payload)

    top_names = "、".join(f"{r['name']} {r['fees_usd']/1e4:.0f}万" for r in payload["top"][:7]) or "—"
    print(
        f"    day={day} universe={payload['universe']} "
        f"<1k={payload['lt_1k']} <10={payload['lt_10']} =0={payload['eq_0']} "
        f">=100k={payload['ge_100k']} [{top_names}] {time.time() - started:.1f}s"
    )
    print(f"json -> {dest}")
    print(f"hist -> {hist}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
