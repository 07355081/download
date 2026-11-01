#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BTC 全网算力 7 日均 + Puell Multiple（免费已有源）。

- 算力：mining-shutdown-price 已落盘的 mempool 日序列 → 7 日均（EH/s）
- Puell：btc-index CoinGlass puell-multiple
不含 CryptoQuant 矿工持仓总量（看板无、无免费同源）。

用法：
  python download.py
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "output" / "json" / "btc_hashrate_puell.json"

HASH_CANDIDATES = [
    ROOT / "mining-shutdown-price" / "output" / "csv" / "bitinfocharts_btc_hashrate_hs.csv",
    Path("/root/data-download/mining-shutdown-price/output/csv/bitinfocharts_btc_hashrate_hs.csv"),
]
PUELL_CANDIDATES = [
    Path("/root/dashboard/public/json/btc-index/coinglass/puell-multiple.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "btc-index" / "coinglass" / "puell-multiple.json",
    ROOT / "btc-index" / "output" / "json" / "coinglass" / "puell-multiple.json",
]


def resolve(cands: list[Path]) -> Path:
    for path in cands:
        if path.is_file():
            return path
    raise FileNotFoundError(cands[0].name)


def load_hashrate_eh() -> list[dict[str, Any]]:
    path = resolve(HASH_CANDIDATES)
    rows: list[tuple[str, float]] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            day = str(row.get("date") or "")[:10]
            raw = row.get("hashrate_hs")
            if not day or raw is None:
                continue
            try:
                eh = float(raw) / 1e18
            except (TypeError, ValueError):
                continue
            if eh <= 0:
                continue
            rows.append((day, eh))
    rows.sort(key=lambda x: x[0])
    # 7d MA
    out: list[dict[str, Any]] = []
    for i in range(len(rows)):
        if i < 6:
            continue
        window = rows[i - 6 : i + 1]
        ma = sum(v for _, v in window) / 7.0
        day, spot = rows[i]
        item: dict[str, Any] = {
            "day": day,
            "hashrate_eh": round(spot, 4),
            "hashrate_7d_eh": round(ma, 4),
        }
        if i >= 13:
            prev = sum(v for _, v in rows[i - 13 : i - 6]) / 7.0
            item["wow_eh"] = round(ma - prev, 4)
            item["wow_pct"] = round((ma / prev - 1.0) * 100.0, 2) if prev > 0 else None
        out.append(item)
    return out


def load_puell() -> list[dict[str, Any]]:
    path = resolve(PUELL_CANDIDATES)
    doc = json.loads(path.read_text(encoding="utf-8"))
    data = doc.get("data") if isinstance(doc, dict) else doc
    out: list[dict[str, Any]] = []
    if isinstance(data, dict) and isinstance(data.get("time_list"), list):
        times = data["time_list"]
        vals = data.get("data_list") or []
        for ts, item in zip(times, vals):
            try:
                ts_i = int(ts)
                if ts_i > 1e12:
                    ts_i //= 1000
                day = datetime.fromtimestamp(ts_i, tz=timezone.utc).date().isoformat()
            except (TypeError, ValueError, OSError):
                continue
            if not isinstance(item, dict):
                continue
            val = item.get("puell_multiple")
            if not isinstance(val, (int, float)):
                continue
            out.append({"day": day, "puell": float(val)})
    elif isinstance(data, list):
        for row in data:
            if not isinstance(row, dict):
                continue
            day = str(row.get("day") or row.get("date") or "")[:10]
            val = row.get("puell_multiple") or row.get("value") or row.get("puell")
            if not day or not isinstance(val, (int, float)):
                continue
            out.append({"day": day, "puell": float(val)})
    out.sort(key=lambda x: x["day"])
    for i, row in enumerate(out):
        if i >= 7:
            prev = out[i - 7]["puell"]
            row["wow"] = round(row["puell"] - prev, 4)
    return out


def main() -> int:
    hash_rows = load_hashrate_eh()
    puell_rows = load_puell()
    if not hash_rows:
        raise RuntimeError("算力序列为空")
    latest = dict(hash_rows[-1])
    puell_by = {r["day"]: r for r in puell_rows}
    # align puell to latest hashrate day or last available ≤ day
    p = puell_by.get(latest["day"])
    if p is None:
        earlier = [r for r in puell_rows if r["day"] <= latest["day"]]
        p = earlier[-1] if earlier else None
    if p:
        latest["puell"] = p.get("puell")
        latest["puell_day"] = p.get("day")
        latest["puell_wow"] = p.get("wow")
    ath = max(r["hashrate_7d_eh"] for r in hash_rows)
    latest["is_7d_ath"] = latest["hashrate_7d_eh"] >= ath - 1e-6
    doc = {
        "name": "btc_hashrate_puell",
        "source": "mempool.space hashrate + coinglass puell-multiple",
        "note": (
            "算力为 mempool 日均 H/s 转 EH/s 后的 7 日简单平均；"
            "Puell 为 CoinGlass。不含 CryptoQuant 矿工持仓总量。"
        ),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest": latest,
        "history": hash_rows[-400:],
        "puell_history": puell_rows[-400:],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT)
    print(
        f"wrote {OUT} day={latest['day']} ma7={latest['hashrate_7d_eh']} "
        f"wow={latest.get('wow_eh')} ath={latest['is_7d_ath']} "
        f"puell={latest.get('puell')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
