#!/usr/bin/env python3
"""各链 24h 应用收入（DefiLlama dailyRevenue 按链加总）→ 落盘。

不是公链自己的 gas。把每个协议 breakdown24h 里的收入按链相加。
不含 off_chain。

输出：
  output/json/chain_app_revenue_latest.json
  output/json/chain_app_revenue_history/YYYY-MM-DD.json
"""
from __future__ import annotations

import json
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
JSON_DIR = HERE / "output" / "json"
LATEST_PATH = JSON_DIR / "chain_app_revenue_latest.json"
HISTORY_DIR = JSON_DIR / "chain_app_revenue_history"

URL = (
    "https://api.llama.fi/overview/fees"
    "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyRevenue"
)
SOURCE_URL = "https://defillama.com/chains"
ATTEMPTS = 4
TIMEOUT = 120
TOP_N = 12
TZ_CN = timezone(timedelta(hours=8))

NAMES = {
    "robinhood": "Robinhood Chain",
    "solana": "Solana",
    "ethereum": "Ethereum",
    "hyperliquid": "Hyperliquid L1",
    "bsc": "BNB Chain",
    "base": "Base",
    "polygon": "Polygon",
    "arbitrum": "Arbitrum",
    "tron": "Tron",
    "avalanche": "Avalanche",
    "ton": "TON",
    "near": "Near",
    "bitcoin": "Bitcoin",
    "zklighter": "zkLighter",
    "edgex": "edgeX",
}


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def shanghai_day() -> str:
    return datetime.now(TZ_CN).date().isoformat()


def http_get_json(url: str) -> dict:
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "dashboard-data/chain-app-revenue"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < ATTEMPTS:
                time.sleep(min(2**attempt, 15))
    raise RuntimeError(f"GET failed after {ATTEMPTS} attempts: {last}")


def leaves(obj: object) -> float:
    if isinstance(obj, (int, float)):
        return float(obj)
    if isinstance(obj, dict):
        return sum(leaves(v) for v in obj.values())
    return 0.0


def display_name(chain_id: str) -> str:
    return NAMES.get(chain_id, chain_id.replace("_", " ").title())


def build_snapshot(doc: dict[str, Any], *, day: str) -> dict[str, Any]:
    totals: dict[str, float] = defaultdict(float)
    for row in doc.get("protocols") or []:
        if not isinstance(row, dict):
            continue
        breakdown = row.get("breakdown24h")
        if not isinstance(breakdown, dict):
            continue
        for chain_id, payload in breakdown.items():
            key = str(chain_id).strip().lower()
            if not key or key in {"off_chain", "offchain", "off-chain"}:
                continue
            amount = leaves(payload)
            if amount > 0:
                totals[key] += amount
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    chains = [
        {
            "id": chain_id,
            "name": display_name(chain_id),
            "revenue_usd": round(amount, 2),
            "rank": index,
        }
        for index, (chain_id, amount) in enumerate(ranked, start=1)
    ]
    return {
        "code": "0",
        "msg": "success",
        "name": "chain_app_revenue",
        "description": "各链 24h 应用收入：DefiLlama dailyRevenue 按链加总，不含公链 gas，不含 off_chain",
        "source": "defillama",
        "source_url": SOURCE_URL,
        "updated_at": now_utc_iso(),
        "day": day,
        "chain_count": len(chains),
        "top": chains[:TOP_N],
        "chains": chains,
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def latest_top_from(top: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in top:
        usd = row.get("revenue_usd")
        if not isinstance(usd, (int, float)):
            continue
        name = str(row.get("name") or row.get("id") or "")
        if not name:
            continue
        out.append({"symbol": name, "name": name, "usd": float(usd)})
    return out


def build_series(history_dir: Path, chain_ids: list[str], keep: int = 180) -> list[dict[str, Any]]:
    files = sorted(history_dir.glob("????-??-??.json"))[-keep:]
    buckets: dict[str, list[dict[str, Any]]] = {cid: [] for cid in chain_ids}
    prev: dict[str, float | None] = {cid: None for cid in chain_ids}
    labels: dict[str, str] = {}
    for path in files:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(doc, dict):
            continue
        day = str(doc.get("day") or path.stem)
        by_id: dict[str, dict[str, Any]] = {}
        for row in list(doc.get("top") or []) + list(doc.get("chains") or []):
            if isinstance(row, dict) and row.get("id"):
                by_id[str(row["id"])] = row
        for cid in chain_ids:
            row = by_id.get(cid)
            if not row:
                continue
            val = row.get("revenue_usd")
            if not isinstance(val, (int, float)):
                continue
            last = prev[cid]
            mom = None if last in (None, 0) else round((float(val) / float(last) - 1.0) * 100.0, 4)
            buckets[cid].append({"month": day, "usd": float(val), "mom_pct": mom})
            prev[cid] = float(val)
            labels[cid] = str(row.get("name") or display_name(cid))
    out: list[dict[str, Any]] = []
    for cid in chain_ids:
        months = buckets[cid]
        if len(months) < 2:
            continue
        name = labels.get(cid) or display_name(cid)
        out.append({"label": name, "labelEn": name, "months": months})
    return out


def main() -> int:
    print("[chain-app-revenue] DefiLlama dailyRevenue by chain")
    started = time.time()
    doc = http_get_json(URL)
    day = shanghai_day()
    payload = build_snapshot(doc, day=day)
    if payload["chain_count"] < 5:
        raise RuntimeError(f"只有 {payload['chain_count']} 条链，不写文件")
    hist = HISTORY_DIR / f"{day}.json"
    write_json(hist, payload)
    payload["latest_top"] = latest_top_from(payload["top"])
    payload["series"] = build_series(
        HISTORY_DIR,
        [str(row["id"]) for row in payload["top"][:5] if row.get("id")],
    )
    write_json(LATEST_PATH, payload)
    top = "、".join(
        f"{row['name']} {row['revenue_usd']/1e4:.1f}万" for row in payload["top"][:5]
    )
    print(
        f"    day={day} chains={payload['chain_count']} [{top}] "
        f"series={len(payload['series'])} {time.time()-started:.1f}s"
    )
    print(f"json -> {LATEST_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
