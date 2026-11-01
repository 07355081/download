#!/usr/bin/env python3
"""比特币网络活动近邻（免费源）。

- CoinMetrics 社区 TxCnt：BTC 日交易笔数
- mempool.space：当前内存池待处理笔数快照

不含 CryptoQuant 微交易占比、OP_RETURN/Runes 等专有口径。不占 Dune。

输出：output/json/btc_network.json
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
OUT_PATH = HERE / "output" / "json" / "btc_network.json"
UA = "dashboard-data/btc-network"
CM_BASE = (
    "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
    "?assets=btc&metrics=TxCnt&frequency=1d&start_time=2023-01-01&page_size=10000"
)
MEMPOOL_URL = "https://mempool.space/api/mempool"
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


def fetch_txcnt() -> list[dict[str, Any]]:
    url: str | None = CM_BASE
    rows: list[dict[str, Any]] = []
    while url:
        payload = http_json(url)
        if not isinstance(payload, dict):
            raise RuntimeError("CoinMetrics 返回非对象")
        for item in payload.get("data") or []:
            if not isinstance(item, dict):
                continue
            raw = item.get("TxCnt")
            if raw in (None, ""):
                continue
            day = str(item.get("time") or "")[:10]
            if len(day) < 10:
                continue
            rows.append({"day": day, "tx_count": int(float(raw))})
        next_url = payload.get("next_page_url")
        url = str(next_url) if next_url else None
        if url:
            time.sleep(0.25)
    by_day = {row["day"]: row for row in rows}
    out = [by_day[k] for k in sorted(by_day)]
    if len(out) < 200:
        raise RuntimeError(f"TxCnt 只有 {len(out)} 天")
    prev = None
    peak = 0
    peak_day = ""
    for row in out:
        tx = int(row["tx_count"])
        if prev is not None and prev > 0:
            row["dod_pct"] = round((tx / prev - 1) * 100.0, 2)
        else:
            row["dod_pct"] = None
        if tx >= peak:
            peak = tx
            peak_day = str(row["day"])
        row["vs_peak_pct"] = round((tx / peak - 1) * 100.0, 2) if peak else None
        row["peak_day"] = peak_day
        row["peak_tx_count"] = peak
        prev = tx
    return out


def fetch_mempool() -> dict[str, Any]:
    doc = http_json(MEMPOOL_URL)
    if not isinstance(doc, dict):
        raise RuntimeError("mempool 返回非对象")
    count = doc.get("count")
    if not isinstance(count, (int, float)):
        raise RuntimeError(f"mempool count 无效: {count}")
    return {
        "asof": now_iso(),
        "count": int(count),
        "vsize": int(doc.get("vsize") or 0),
        "total_fee": float(doc.get("total_fee") or 0),
    }


def main() -> int:
    days = fetch_txcnt()
    mempool = fetch_mempool()
    # drop incomplete today if CoinMetrics already has today with partial
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    complete = [row for row in days if row["day"] < today]
    if not complete:
        complete = days
    latest = complete[-1]
    payload = {
        "meta": {
            "source": "coinmetrics_community_TxCnt + mempool.space",
            "note": (
                "日交易笔数为 CoinMetrics TxCnt。"
                "内存池为 mempool.space 瞬时快照。"
                "不含微交易占比、OP_RETURN、Runes/Ordinals 等 CryptoQuant 专有指标。"
            ),
            "updated_at": now_iso(),
        },
        "mempool": mempool,
        "latest_day": latest,
        "data": complete,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(
        f"[btc-network] day={latest['day']} tx={latest['tx_count']} "
        f"dod={latest.get('dod_pct')} mempool={mempool['count']} → {OUT_PATH} ({len(complete)}天)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
