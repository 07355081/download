#!/usr/bin/env python3
"""Aave v3 Ethereum 池内 WETH 市值日序列（DefiLlama 近似）。

GET /protocol/aave-v3 → chainTvls.Ethereum.tokensInUsd[*].tokens.WETH
是池内抵押/供给折美元近似，不是 available liquidity，也不能证成「全 DeFi 最深 WETH 池」。
每日只落盘 WETH 序列，不缓存整份 ~30MB 协议 JSON。

输出：output/json/aave_v3_weth.json
"""
from __future__ import annotations

import gzip
import json
import io
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "aave_v3_weth.json"
URL = "https://api.llama.fi/protocol/aave-v3"
UA = "dashboard-data/aave-v3-weth"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_protocol() -> dict[str, Any]:
    req = urllib.request.Request(
        URL,
        headers={
            "User-Agent": UA,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
        encoding = (resp.headers.get("Content-Encoding") or "").lower()
    if encoding == "gzip" or raw[:2] == b"\x1f\x8b":
        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    doc = json.loads(raw.decode("utf-8"))
    if not isinstance(doc, dict):
        raise RuntimeError("protocol 返回非对象")
    return doc


def extract_weth(doc: dict[str, Any]) -> list[dict[str, Any]]:
    eth = (doc.get("chainTvls") or {}).get("Ethereum") or {}
    series = eth.get("tokensInUsd")
    if not isinstance(series, list) or len(series) < 30:
        raise RuntimeError("Ethereum.tokensInUsd 不足")
    out: list[dict[str, Any]] = []
    prev: float | None = None
    for row in series:
        if not isinstance(row, dict):
            continue
        ts = row.get("date")
        tokens = row.get("tokens") or {}
        if not isinstance(tokens, dict):
            continue
        weth = tokens.get("WETH")
        if not isinstance(weth, (int, float)) or weth <= 0:
            continue
        # 过滤明显坏点（如数美元级）
        if weth < 1_000_000:
            continue
        day = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        usd = float(weth)
        dod = None
        if prev and prev > 0:
            dod = round(100.0 * (usd - prev) / prev, 2)
        out.append({"day": day, "weth_usd": round(usd, 2), "dod_pct": dod})
        prev = usd
    # 同日去重取最后
    by_day: dict[str, dict[str, Any]] = {}
    for row in out:
        by_day[row["day"]] = row
    days = [by_day[k] for k in sorted(by_day)]
    if len(days) < 30:
        raise RuntimeError(f"有效 WETH 日点过少: {len(days)}")
    return days


def main() -> int:
    print("GET", URL)
    doc = fetch_protocol()
    days = extract_weth(doc)
    latest = days[-1]
    payload = {
        "code": "0",
        "msg": "success",
        "name": "aave_v3_weth",
        "description": "DefiLlama Aave v3 Ethereum 池内 WETH 市值近似。非 available liquidity。",
        "source": "defillama-aave-v3",
        "source_url": "https://defillama.com/protocol/aave-v3",
        "updated_at": now_iso(),
        "day_count": len(days),
        "days": days,
        "latest": latest,
        "series": [
            {
                "label": "Aave v3 ETH 池内 WETH",
                "labelEn": "Aave v3 ETH WETH in pool",
                "months": [{"month": row["day"], "usd": row["weth_usd"]} for row in days],
            }
        ],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(
        f"wrote {OUT_PATH} days={len(days)} latest={latest['day']} "
        f"weth={latest['weth_usd']/1e8:.2f}亿"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
