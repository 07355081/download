# -*- coding: utf-8 -*-
"""Download global + major-chain DEX spot volume from DefiLlama (free, no Dune).

口径：DefiLlama overview/dexs dailyVolume，不是 CryptoRank。数字可能与外电不一致。

Outputs:
  output/json/dex_volume.json
    data: [{time, global, robinhood, solana, ...}, ...]  # USD, daily
    months / quarters: 完整 UTC 月/季合计与环比

Usage:
  python download.py
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from calendar import monthrange
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
JSON_DIR = HERE / "output" / "json"
OUT_PATH = JSON_DIR / "dex_volume.json"

API = "https://api.llama.fi"
UA = "dashboard-data/dex-volume"
TIMEOUT = 90
ATTEMPTS = 3
SLEEP = 0.4
# DefiLlama path slug → 内部列名 / 展示名
CHAINS: list[tuple[str, str, str]] = [
    ("Robinhood", "robinhood", "Robinhood Chain"),
    ("Solana", "solana", "Solana"),
    ("Ethereum", "ethereum", "Ethereum"),
    ("Base", "base", "Base"),
    ("BSC", "bsc", "BNB Chain"),
    ("Arbitrum", "arbitrum", "Arbitrum"),
    ("Hyperliquid L1", "hyperliquid_l1", "Hyperliquid L1"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get_json(url: str) -> Any:
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < ATTEMPTS:
                time.sleep(min(2**attempt, 12))
    raise RuntimeError(f"GET {url} failed: {last}")


def chart_to_map(chart: list[Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for pt in chart or []:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        ts, vol = pt[0], pt[1]
        if not isinstance(ts, (int, float)) or not isinstance(vol, (int, float)):
            continue
        day = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        out[day] = float(vol)
    return out


def fetch_overview(chain: str | None = None) -> dict[str, float]:
    if chain:
        url = (
            f"{API}/overview/dexs/{urllib.parse.quote(chain)}"
            "?excludeTotalDataChart=false&excludeTotalDataChartBreakdown=true"
            "&dataType=dailyVolume"
        )
    else:
        url = (
            f"{API}/overview/dexs"
            "?excludeTotalDataChart=false&excludeTotalDataChartBreakdown=true"
            "&dataType=dailyVolume"
        )
    payload = http_get_json(url)
    return chart_to_map(payload.get("totalDataChart") if isinstance(payload, dict) else [])


def pct_change(curr: float, prev: float) -> float | None:
    if prev == 0:
        return None
    return (curr / prev - 1.0) * 100.0


def complete_months(day_map: dict[str, float]) -> list[dict[str, Any]]:
    by_m: dict[str, float] = defaultdict(float)
    days_in_m: dict[str, set[str]] = defaultdict(set)
    for day, vol in day_map.items():
        ym = day[:7]
        by_m[ym] += vol
        days_in_m[ym].add(day)
    rows: list[dict[str, Any]] = []
    prev: float | None = None
    for ym in sorted(by_m):
        y, m = int(ym[:4]), int(ym[5:7])
        need = monthrange(y, m)[1]
        if len(days_in_m[ym]) < need:
            continue
        total = by_m[ym]
        mom = pct_change(total, prev) if prev is not None else None
        rows.append(
            {
                "month": ym,
                "volume_usd": round(total, 2),
                "mom_pct": None if mom is None else round(mom, 4),
            }
        )
        prev = total
    return rows


def complete_quarters(months: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_m = {r["month"]: float(r["volume_usd"]) for r in months}
    rows: list[dict[str, Any]] = []
    prev: float | None = None
    years = sorted({int(m[:4]) for m in by_m})
    for year in years:
        for q in (1, 2, 3, 4):
            keys = [f"{year}-{(q - 1) * 3 + i:02d}" for i in (1, 2, 3)]
            if any(k not in by_m for k in keys):
                continue
            total = sum(by_m[k] for k in keys)
            qoq = pct_change(total, prev) if prev is not None else None
            rows.append(
                {
                    "quarter": f"{year}-Q{q}",
                    "volume_usd": round(total, 2),
                    "qoq_pct": None if qoq is None else round(qoq, 4),
                    "months": keys,
                }
            )
            prev = total
    return rows


def main() -> None:
    print("[dex-volume] fetch global DEX dailyVolume…")
    global_map = fetch_overview(None)
    time.sleep(SLEEP)

    chain_maps: dict[str, dict[str, float]] = {}
    for slug, key, label in CHAINS:
        print(f"  chain {slug} ({label})…")
        try:
            chain_maps[key] = fetch_overview(slug)
            print(f"    points={len(chain_maps[key])}")
        except Exception as exc:  # noqa: BLE001
            print(f"    WARN {slug}: {exc}")
            chain_maps[key] = {}
        time.sleep(SLEEP)

    all_days = sorted(set(global_map) | {d for m in chain_maps.values() for d in m})

    data: list[dict[str, Any]] = []
    for day in all_days:
        row: dict[str, Any] = {"time": day}
        if day in global_map:
            row["global"] = global_map[day]
        for key, series in chain_maps.items():
            if day in series:
                row[key] = series[day]
        if len(row) > 1:
            data.append(row)

    # 月/季用完整全球序列（不截断 KEEP 前的完整月，再取尾部）
    months_global = complete_months(global_map)
    quarters_global = complete_quarters(months_global)
    chain_quarters: dict[str, list[dict[str, Any]]] = {}
    for key, series in chain_maps.items():
        chain_quarters[key] = complete_quarters(complete_months(series))

    def chart_series(key: str, label: str, label_en: str) -> dict[str, Any] | None:
        months: list[dict[str, Any]] = []
        prev: float | None = None
        for row in data:
            day = row.get("time")
            val = row.get(key)
            if not isinstance(day, str) or not isinstance(val, (int, float)):
                continue
            mom = pct_change(float(val), prev) if prev is not None else None
            months.append(
                {
                    "month": day,
                    "usd": float(val),
                    "mom_pct": None if mom is None else round(mom, 4),
                }
            )
            prev = float(val)
        if len(months) < 2:
            return None
        return {"label": label, "labelEn": label_en, "months": months}

    series_blocks = [
        block
        for block in (
            chart_series("global", "全球 DEX 现货", "Global DEX spot"),
            chart_series("solana", "Solana", "Solana"),
            chart_series("ethereum", "Ethereum", "Ethereum"),
        )
        if block
    ]

    latest = data[-1] if data else {}
    top_today = sorted(
        (
            {"key": k, "label": lab, "volume_usd": float(latest[k])}
            for _slug, k, lab in CHAINS
            if isinstance(latest.get(k), (int, float))
        ),
        key=lambda x: x["volume_usd"],
        reverse=True,
    )

    payload = {
        "code": "0",
        "msg": "success",
        "name": "dex_volume",
        "description": "全球与主力链 DEX 现货日成交（DefiLlama dailyVolume）",
        "source": "defillama",
        "source_url": "https://defillama.com/dexs",
        "metric": "dailyVolume",
        "note": (
            "DefiLlama DEX 现货成交口径，与 CryptoRank 等第三方全行业统计可能相差较大，"
            "发稿须写明 DefiLlama，勿标 CryptoRank。"
        ),
        "updated_at": now_iso(),
        "chains": [{"slug": s, "key": k, "label": lab} for s, k, lab in CHAINS],
        "row_count": len(data),
        "latest_day": latest.get("time"),
        "latest_global": latest.get("global"),
        "latest_chains": top_today,
        "months": months_global[-36:],
        "quarters": quarters_global[-16:],
        "chain_quarters": {k: v[-12:] for k, v in chain_quarters.items()},
        "series": series_blocks,
        "data": data,
    }

    JSON_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(OUT_PATH)
    q3 = next((q for q in reversed(quarters_global) if q["quarter"] == "2026-Q3"), None)
    print(
        f"[dex-volume] wrote {OUT_PATH} rows={len(data)} "
        f"latest={latest.get('time')} global={latest.get('global')} "
        f"Q3_2026={None if q3 is None else q3['volume_usd']}"
    )


if __name__ == "__main__":
    main()
