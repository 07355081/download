# -*- coding: utf-8 -*-
"""Pareto FalconX Credit Vault TVL（DefiLlama Yields，免费）。

找 project=pareto-credit 且 poolMeta 含 FalconX 的池；拉 chart 作日序列。
非 Token Terminal；数字可能与外电不一致。

输出：output/json/falconx_vault.json
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT = HERE / "output" / "json" / "falconx_vault.json"
POOLS_URL = "https://yields.llama.fi/pools"
CHART_URL = "https://yields.llama.fi/chart/{pool_id}"
UA = "dashboard-data/falconx-vault"
TIMEOUT = 45
ATTEMPTS = 4


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get_json(url: str) -> Any:
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = exc
            if attempt < ATTEMPTS and exc.code in (429, 500, 502, 503, 504):
                time.sleep(min(2**attempt, 20))
                continue
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < ATTEMPTS:
                time.sleep(min(2**attempt, 12))
                continue
            raise
    raise RuntimeError(f"GET failed: {last}")


def find_pool(pools: list[dict[str, Any]]) -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    for row in pools:
        if not isinstance(row, dict):
            continue
        if str(row.get("project") or "").lower() != "pareto-credit":
            continue
        meta = str(row.get("poolMeta") or "")
        if "falconx" not in meta.lower():
            continue
        hits.append(row)
    if not hits:
        raise RuntimeError("找不到 pareto-credit / FalconX 池")
    hits.sort(key=lambda r: float(r.get("tvlUsd") or 0), reverse=True)
    return hits[0]


def main() -> None:
    doc = http_get_json(POOLS_URL)
    pools = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(pools, list):
        raise RuntimeError("yields pools 无效")
    pool = find_pool(pools)
    pool_id = str(pool.get("pool") or "")
    tvl = float(pool.get("tvlUsd") or 0)
    if not pool_id or tvl <= 0:
        raise RuntimeError(f"池无效: {pool_id} {tvl}")

    chart = http_get_json(CHART_URL.format(pool_id=pool_id))
    series_raw = chart.get("data") if isinstance(chart, dict) else None
    by_day: dict[str, float] = {}
    if isinstance(series_raw, list):
        for row in series_raw:
            if not isinstance(row, dict):
                continue
            ts = str(row.get("timestamp") or "")[:10]
            usd = row.get("tvlUsd")
            if len(ts) == 10 and isinstance(usd, (int, float)) and float(usd) > 0:
                by_day[ts] = float(usd)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    by_day[day] = tvl

    rows = [{"time": d, "tvl_usd": by_day[d]} for d in sorted(by_day)]
    peak = max(float(r["tvl_usd"]) for r in rows)
    latest = rows[-1]
    latest["peak_tvl_usd"] = peak
    latest["vs_peak_pct"] = (float(latest["tvl_usd"]) - peak) / peak * 100.0 if peak else None
    if len(rows) >= 2:
        prev = float(rows[-2]["tvl_usd"])
        latest["dod_pct"] = (float(latest["tvl_usd"]) - prev) / prev * 100.0 if prev else None
    else:
        latest["dod_pct"] = None

    # YTD vs first point of current year in series
    year = day[:4]
    ytd_rows = [r for r in rows if str(r["time"]).startswith(year)]
    if ytd_rows:
        base = float(ytd_rows[0]["tvl_usd"])
        latest["ytd_pct"] = (float(latest["tvl_usd"]) - base) / base * 100.0 if base else None
        latest["ytd_base_day"] = ytd_rows[0]["time"]
        latest["ytd_base_tvl_usd"] = base
    else:
        latest["ytd_pct"] = None

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "source": "defillama_yields",
            "project": "pareto-credit",
            "pool_meta": pool.get("poolMeta"),
            "pool_id": pool_id,
            "symbol": pool.get("symbol"),
            "chain": pool.get("chain"),
            "note": "Pareto Credit 下 FalconX 池 TVL。非 Token Terminal。",
            "updated_at": now_iso(),
            "as_of": day,
        },
        "data": rows,
        "latest": latest,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[falconx-vault] day={day} tvl={tvl/1e8:.2f}亿 peak={peak/1e8:.2f}亿 "
        f"ytd={latest.get('ytd_pct')} n={len(rows)} → {OUT}"
    )


if __name__ == "__main__":
    main()
