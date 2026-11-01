#!/usr/bin/env python3
"""RLUSD 以太坊 / XRPL 流通量日序列（DefiLlama）。

GET stablecoincharts/ethereum?stablecoin=250
GET stablecoincharts/xrpl?stablecoin=250

不含转账额。不占 Dune。

输出：output/json/rlusd_chain.json
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
OUT_PATH = HERE / "output" / "json" / "rlusd_chain.json"
UA = "dashboard-data/rlusd-chain"
API = "https://stablecoins.llama.fi/stablecoincharts"
COIN_ID = "250"
CHAINS = (("ethereum", "eth_usd"), ("xrpl", "xrpl_usd"))
TIMEOUT = 60
ATTEMPTS = 4


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_json(url: str) -> Any:
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < ATTEMPTS:
                time.sleep(min(2**attempt, 12))
                continue
            raise
    raise RuntimeError(f"GET failed: {last}")


def day_of(raw: Any) -> str | None:
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw), timezone.utc).strftime("%Y-%m-%d")
    text = str(raw or "").strip()
    if text.isdigit():
        return datetime.fromtimestamp(int(text), timezone.utc).strftime("%Y-%m-%d")
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None


def circulating_usd(row: dict[str, Any]) -> float | None:
    for key in ("totalCirculatingUSD", "totalCirculating"):
        blob = row.get(key)
        if isinstance(blob, dict) and isinstance(blob.get("peggedUSD"), (int, float)):
            return float(blob["peggedUSD"])
    return None


def fetch_chain(slug: str) -> dict[str, float]:
    doc = http_json(f"{API}/{slug}?stablecoin={COIN_ID}")
    if not isinstance(doc, list):
        raise RuntimeError(f"{slug} 返回非数组")
    out: dict[str, float] = {}
    for row in doc:
        if not isinstance(row, dict):
            continue
        day = day_of(row.get("date"))
        usd = circulating_usd(row)
        if day and usd is not None and usd > 0:
            out[day] = usd
    if not out:
        raise RuntimeError(f"{slug} 无有效点")
    return out


def main() -> int:
    series: dict[str, dict[str, float]] = {}
    for slug, _field in CHAINS:
        series[slug] = fetch_chain(slug)
        time.sleep(0.35)
    days = sorted(set(series["ethereum"]) | set(series["xrpl"]))
    rows: list[dict[str, Any]] = []
    for day in days:
        eth = series["ethereum"].get(day)
        xrpl = series["xrpl"].get(day)
        if eth is None or xrpl is None:
            continue
        total = eth + xrpl
        if total <= 0:
            continue
        rows.append(
            {
                "day": day,
                "eth_usd": round(eth, 2),
                "xrpl_usd": round(xrpl, 2),
                "total_usd": round(total, 2),
                "eth_share_pct": round(eth / total * 100.0, 2),
                "xrpl_share_pct": round(xrpl / total * 100.0, 2),
            }
        )
    if len(rows) < 30:
        raise RuntimeError(f"合并后只有 {len(rows)} 天")
    # dod share
    prev = None
    for row in rows:
        if prev is not None:
            row["eth_share_dod_pp"] = round(row["eth_share_pct"] - prev["eth_share_pct"], 2)
            row["total_dod_pct"] = round((row["total_usd"] / prev["total_usd"] - 1) * 100.0, 2)
        else:
            row["eth_share_dod_pp"] = None
            row["total_dod_pct"] = None
        prev = row

    payload = {
        "meta": {
            "source": "defillama",
            "coin": "RLUSD",
            "coin_id": COIN_ID,
            "chains": ["ethereum", "xrpl"],
            "note": "流通量=DefiLlama peggedUSD。占比仅 ETH+XRPL 两链合计，不含其它链。无转账额。",
            "updated_at": now_iso(),
        },
        "data": rows,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    latest = rows[-1]
    print(
        f"[rlusd-chain] {latest['day']} total={latest['total_usd']/1e8:.2f}亿 "
        f"eth={latest['eth_share_pct']}% xrpl={latest['xrpl_share_pct']}% → {OUT_PATH} ({len(rows)}天)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
