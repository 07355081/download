#!/usr/bin/env python3
"""比特币网络活动近邻（免费源）。

- CoinMetrics 社区 TxCnt：BTC 日交易笔数（原口径，不改）
- CoinMetrics 社区 TxTfrCnt：BTC 日转账笔数（目录里有、频率 1d）
- mempool.space：当前待处理笔数，以及 /api/v1/statistics/1y 历史曲线

社区目录没有微交易占比，也没有 OP_RETURN 拆分。不占 Dune。

输出：output/json/btc_network.json
"""
from __future__ import annotations

import json
import ssl
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
    "?assets=btc&metrics=TxCnt,TxTfrCnt&frequency=1d&start_time=2023-01-01&page_size=10000"
)
MEMPOOL_URL = "https://mempool.space/api/mempool"
MEMPOOL_HISTORY_URL = "https://mempool.space/api/v1/statistics/1y"
TIMEOUT = 60
ATTEMPTS = 4


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ssl_contexts() -> list[ssl.SSLContext]:
    contexts = [ssl.create_default_context()]
    try:
        import certifi
    except ImportError:
        return contexts
    contexts.append(ssl.create_default_context(cafile=certifi.where()))
    return contexts


def http_json(url: str) -> Any:
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        for ctx in ssl_contexts():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.URLError as exc:
                last = exc
                if isinstance(getattr(exc, "reason", None), ssl.SSLError):
                    continue
                break
            except ssl.SSLError as exc:
                last = exc
                continue
            except Exception as exc:  # noqa: BLE001
                last = exc
                break
        if attempt < ATTEMPTS:
            time.sleep(min(2**attempt, 12))
            continue
        raise RuntimeError(f"GET failed: {last}")
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
            row: dict[str, Any] = {"day": day, "tx_count": int(float(raw))}
            transfers = item.get("TxTfrCnt")
            if transfers not in (None, ""):
                row["tfr_count"] = int(float(transfers))
            rows.append(row)
        next_url = payload.get("next_page_url")
        url = str(next_url) if next_url else None
        if url:
            time.sleep(0.25)
    by_day = {row["day"]: row for row in rows}
    out = [by_day[k] for k in sorted(by_day)]
    if len(out) < 200:
        raise RuntimeError(f"TxCnt 只有 {len(out)} 天")
    with_transfers = sum(1 for row in out if isinstance(row.get("tfr_count"), int))
    if with_transfers < 200:
        raise RuntimeError(f"TxTfrCnt 只有 {with_transfers} 天")
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


def fetch_mempool_history() -> list[dict[str, Any]]:
    doc = http_json(MEMPOOL_HISTORY_URL)
    if not isinstance(doc, list) or not doc:
        raise RuntimeError("mempool 历史不是数组")
    points: list[dict[str, Any]] = []
    for item in doc:
        if not isinstance(item, dict):
            continue
        added = item.get("added")
        count = item.get("count")
        if not isinstance(added, (int, float)) or not isinstance(count, (int, float)):
            continue
        stamp = datetime.fromtimestamp(float(added), timezone.utc).strftime("%Y-%m-%d %H:%M")
        points.append({"t": stamp, "count": int(round(float(count)))})
    points.sort(key=lambda row: row["t"])
    deduped = {row["t"]: row for row in points}
    out = [deduped[key] for key in sorted(deduped)]
    if len(out) < 100:
        raise RuntimeError(f"mempool 历史只有 {len(out)} 个点")
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
    mempool_history = fetch_mempool_history()
    # drop incomplete today if CoinMetrics already has today with partial
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    complete = [row for row in days if row["day"] < today]
    if not complete:
        complete = days
    latest = complete[-1]
    payload = {
        "meta": {
            "source": "coinmetrics_community_TxCnt_TxTfrCnt + mempool.space",
            "note": (
                "日交易笔数为 CoinMetrics TxCnt，日转账笔数为 CoinMetrics TxTfrCnt。"
                "内存池当前值是 mempool.space 快照，曲线是 statistics/1y。"
                "社区目录没有微交易占比，也没有 OP_RETURN 拆分。"
            ),
            "updated_at": now_iso(),
        },
        "mempool": mempool,
        "mempool_history": mempool_history,
        "latest_day": latest,
        "data": complete,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(
        f"[btc-network] day={latest['day']} tx={latest['tx_count']} "
        f"dod={latest.get('dod_pct')} tfr={latest.get('tfr_count')} "
        f"mempool={mempool['count']} history={len(mempool_history)} → {OUT_PATH} ({len(complete)}天)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
