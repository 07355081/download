# -*- coding: utf-8 -*-
"""X Layer DeFi 锁仓 + Aave/Uniswap 分项（DefiLlama 免费，不占 Dune）。

链锁仓：GET /v2/historicalChainTvl/X Layer
协议：GET /protocol/aave-v3、/protocol/uniswap 的 chainTvls['X Layer']

输出：
  output/json/x_layer.json
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "x_layer.json"
API = "https://api.llama.fi"
UA = "dashboard-data/x-layer"
TIMEOUT = 90
ATTEMPTS = 3
CHAIN = "X Layer"


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


def day_of(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def parse_chain_tvl(rows: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = row.get("date")
        tvl = row.get("tvl")
        if isinstance(ts, (int, float)) and isinstance(tvl, (int, float)):
            out[day_of(int(ts))] = float(tvl)
    return out


def parse_protocol_chain(doc: Any, chain: str = CHAIN) -> dict[str, float]:
    out: dict[str, float] = {}
    if not isinstance(doc, dict):
        return out
    block = (doc.get("chainTvls") or {}).get(chain)
    rows: Any = None
    if isinstance(block, dict):
        rows = block.get("tvl")
    elif isinstance(block, list):
        rows = block
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = row.get("date")
        val = row.get("totalLiquidityUSD", row.get("tvl"))
        if isinstance(ts, (int, float)) and isinstance(val, (int, float)):
            out[day_of(int(ts))] = float(val)
    return out


def chart_series(label: str, label_en: str, series: dict[str, float]) -> dict[str, Any] | None:
    months: list[dict[str, Any]] = []
    prev: float | None = None
    for day in sorted(series):
        val = series[day]
        mom = None if prev in (None, 0) else round((val / float(prev) - 1.0) * 100.0, 4)
        months.append({"month": day, "usd": val, "mom_pct": mom})
        prev = val
    if len(months) < 2:
        return None
    return {"label": label, "labelEn": label_en, "months": months}


def main() -> None:
    print("[x-layer] fetch historicalChainTvl/X Layer…")
    chain = parse_chain_tvl(
        http_get_json(f"{API}/v2/historicalChainTvl/{urllib.parse.quote(CHAIN)}")
    )
    if not chain:
        raise RuntimeError("X Layer 锁仓序列为空")
    time.sleep(0.4)
    print("[x-layer] fetch protocol aave-v3…")
    aave = parse_protocol_chain(http_get_json(f"{API}/protocol/aave-v3"))
    time.sleep(0.4)
    print("[x-layer] fetch protocol uniswap…")
    uni = parse_protocol_chain(http_get_json(f"{API}/protocol/uniswap"))

    def nearest(series: dict[str, float], day: str) -> float | None:
        if day in series:
            return series[day]
        earlier = [d for d in series if d <= day]
        return series[max(earlier)] if earlier else None

    days = sorted(chain)
    history: list[dict[str, Any]] = []
    for day in days:
        row: dict[str, Any] = {"time": day, "tvl": chain[day]}
        aave_v = nearest(aave, day)
        uni_v = nearest(uni, day)
        if aave_v is not None:
            row["aave_tvl"] = aave_v
            if chain[day] > 0:
                row["aave_share_pct"] = aave_v / chain[day] * 100.0
        if uni_v is not None:
            row["uniswap_tvl"] = uni_v
            if chain[day] > 0:
                row["uniswap_share_pct"] = uni_v / chain[day] * 100.0
        history.append(row)

    peak_day, peak_tvl = max(chain.items(), key=lambda item: item[1])
    latest_day = max(chain)
    latest_tvl = chain[latest_day]
    aave_now = nearest(aave, latest_day)
    uni_now = nearest(uni, latest_day)

    series_blocks = [
        block
        for block in (
            chart_series("X Layer DeFi 锁仓", "X Layer DeFi TVL", chain),
            chart_series("Aave V3", "Aave V3", aave) if aave else None,
            chart_series("Uniswap", "Uniswap", uni) if uni else None,
        )
        if block
    ]

    payload = {
        "code": "0",
        "msg": "success",
        "name": "x_layer",
        "source": "defillama",
        "source_url": "https://defillama.com/chain/X%20Layer",
        "note_zh": (
            "X Layer DeFi 锁仓与 Aave/Uniswap 分项来自 DefiLlama，不是 Dune 官方面板；"
            "活跃地址无免费长序列，本稿不含。"
            "数字可能与官网/Dune 略有差异。"
        ),
        "updated_at": now_iso(),
        "as_of": latest_day,
        "peak_day": peak_day,
        "peak_tvl": peak_tvl,
        "latest": {
            "time": latest_day,
            "tvl": latest_tvl,
            "aave_tvl": aave_now,
            "uniswap_tvl": uni_now,
            "aave_share_pct": (aave_now / latest_tvl * 100.0)
            if isinstance(aave_now, (int, float)) and latest_tvl > 0
            else None,
            "uniswap_share_pct": (uni_now / latest_tvl * 100.0)
            if isinstance(uni_now, (int, float)) and latest_tvl > 0
            else None,
        },
        "history": history,
        "series": series_blocks,
        "data": [{"time": d, "tvl": chain[d]} for d in sorted(chain)],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(OUT_PATH)
    aave_txt = "—" if aave_now is None else f"{aave_now/1e6:.2f}M"
    uni_txt = "—" if uni_now is None else f"{uni_now/1e6:.2f}M"
    print(
        f"[x-layer] wrote {OUT_PATH} as_of={latest_day} "
        f"tvl={latest_tvl/1e6:.2f}M aave={aave_txt} uni={uni_txt} peak={peak_day} "
        f"days={len(chain)} series={len(series_blocks)}"
    )


if __name__ == "__main__":
    main()
