# -*- coding: utf-8 -*-
"""用已经落盘的 BTC、ETH 日价，写成季度收益页能画的序列。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

PUB = Path("/root/dashboard/public/json")
OUT = PUB / "quarter-returns" / "quarter_returns.json"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    btc_path = PUB / "btc-index" / "coinglass" / "btc_spot_daily.json"
    eth_path = PUB / "spot-price-history" / "Kraken_ETH_USD_1d.json"
    if not btc_path.is_file() or not eth_path.is_file():
        print("missing price files")
        return 1
    btc_pts = []
    for row in load(btc_path).get("data") or []:
        if isinstance(row, dict) and isinstance(row.get("date"), str) and isinstance(row.get("price"), (int, float)):
            btc_pts.append({"month": row["date"][:10], "usd": float(row["price"])})
    eth_pts = []
    for row in load(eth_path).get("data") or []:
        if not isinstance(row, dict) or not isinstance(row.get("close"), (int, float)):
            continue
        raw = row.get("time")
        if not isinstance(raw, (int, float)):
            continue
        ms = raw if raw > 1e12 else raw * 1000
        day = datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")
        eth_pts.append({"month": day, "usd": float(row["close"])})
    if len(btc_pts) < 2 or len(eth_pts) < 2:
        print("not enough prices")
        return 1
    doc = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "btc_spot_daily + Kraken ETH",
        "series": [
            {"label": "比特币", "labelEn": "Bitcoin", "months": btc_pts[-180:]},
            {"label": "以太坊", "labelEn": "Ethereum", "months": eth_pts[-180:]},
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(OUT)
    print(f"wrote {OUT} btc={len(btc_pts[-180:])} eth={len(eth_pts[-180:])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
