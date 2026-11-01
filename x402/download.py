#!/usr/bin/env python3
"""x402 日交易笔数 / 成交额（免费 x402stats）。

GET https://x402stats.io/api/stats

非 Artemis 同源。无 BlockRun 卖家拆分。不占 Dune。

输出：output/json/x402_stats.json
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "x402_stats.json"
UA = "dashboard-data/x402"
API = "https://x402stats.io/api/stats"
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


def fetch_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    doc = http_json(API)
    if not isinstance(doc, dict):
        raise RuntimeError("x402stats 返回非对象")
    series = doc.get("series")
    if not isinstance(series, list) or len(series) < 30:
        raise RuntimeError(f"series 过短: {type(series)} {len(series) if isinstance(series, list) else ''}")
    rows: list[dict[str, Any]] = []
    for item in series:
        if not isinstance(item, dict):
            continue
        day = str(item.get("date") or "")[:10]
        if len(day) < 10:
            continue
        txs = item.get("transactions")
        vol = item.get("volumeUsd")
        if not isinstance(txs, (int, float)) or not isinstance(vol, (int, float)):
            continue
        txs_i = int(txs)
        vol_f = float(vol)
        avg = round(vol_f / txs_i, 6) if txs_i > 0 else None
        rows.append(
            {
                "day": day,
                "transactions": txs_i,
                "volume_usd": round(vol_f, 4),
                "avg_usd": avg,
                "sellers": int(item["sellers"]) if isinstance(item.get("sellers"), (int, float)) else None,
                "buyers": int(item["buyers"]) if isinstance(item.get("buyers"), (int, float)) else None,
            }
        )
    by_day = {r["day"]: r for r in rows}
    out = [by_day[k] for k in sorted(by_day)]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # 当日多为未收齐，只保留完整日
    complete = [r for r in out if r["day"] < today]
    if len(complete) < 30:
        raise RuntimeError(f"完整日只有 {len(complete)} 天")
    prev_tx = None
    prev_vol = None
    for row in complete:
        tx = int(row["transactions"])
        vol = float(row["volume_usd"])
        if prev_tx and prev_tx > 0:
            row["txs_dod_pct"] = round((tx / prev_tx - 1) * 100.0, 2)
        else:
            row["txs_dod_pct"] = None
        if prev_vol and prev_vol > 0:
            row["vol_dod_pct"] = round((vol / prev_vol - 1) * 100.0, 2)
        else:
            row["vol_dod_pct"] = None
        prev_tx = tx
        prev_vol = vol
    meta = {
        "source": "x402stats.io/api/stats",
        "note": "日笔数与成交额为 x402stats；非 Artemis；不含 BlockRun 拆分。",
        "updated_at": now_iso(),
        "methodologyVersion": doc.get("methodologyVersion"),
        "api_updated_at": doc.get("updatedAt"),
    }
    snap = doc.get("snapshot") if isinstance(doc.get("snapshot"), dict) else None
    return complete, {"meta": meta, "snapshot": snap}


def main() -> int:
    rows, extra = fetch_rows()
    latest = rows[-1]
    payload = {
        **extra,
        "latest_day": latest,
        "data": rows,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(
        f"[x402] day={latest['day']} txs={latest['transactions']} "
        f"vol={latest['volume_usd']:.2f} avg={latest.get('avg_usd')} → {OUT_PATH} ({len(rows)}天)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
