# -*- coding: utf-8 -*-
"""给已有 privacy_coins.json 补 latest_top / data，让飞书页能画图。"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def attach(path: Path) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise SystemExit(f"bad json {path}")
    cat = doc.get("category") if isinstance(doc.get("category"), dict) else {}
    zec = doc.get("zec") if isinstance(doc.get("zec"), dict) else {}
    top = doc.get("top") if isinstance(doc.get("top"), list) else []
    mcap = cat.get("market_cap_usd")
    if not isinstance(mcap, (int, float)):
        raise SystemExit(f"no market_cap {path}")
    ch24 = cat.get("market_cap_change_24h_pct")
    ch24_f = float(ch24) if isinstance(ch24, (int, float)) else None
    as_of = str(doc.get("as_of") or "")[:10]
    day = as_of if len(as_of) == 10 else datetime.utcnow().strftime("%Y-%m-%d")
    zec_mcap = zec.get("market_cap_usd") if isinstance(zec.get("market_cap_usd"), (int, float)) else None
    zec_share = zec.get("share_pct") if isinstance(zec.get("share_pct"), (int, float)) else None

    latest_top: list[dict[str, Any]] = []
    for row in top:
        if not isinstance(row, dict):
            continue
        raw = row.get("market_cap_usd")
        if not isinstance(raw, (int, float)):
            continue
        latest_top.append(
            {
                "symbol": str(row.get("symbol") or "").upper(),
                "market_cap": float(raw),
                "market_cap_rank": row.get("market_cap_rank"),
            }
        )

    data = doc.get("data") if isinstance(doc.get("data"), list) else []
    cleaned: list[dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict) or not row.get("time"):
            continue
        val = row.get("market_cap")
        if not isinstance(val, (int, float)):
            continue
        if str(row["time"]) == day:
            continue
        cleaned.append({"time": str(row["time"]), "market_cap": float(val)})
    today: dict[str, Any] = {"time": day, "market_cap": float(mcap)}
    if ch24_f is not None:
        today["market_cap_change_24h"] = ch24_f
    if isinstance(zec_mcap, (int, float)):
        today["zec_mcap"] = float(zec_mcap)
    if isinstance(zec_share, (int, float)):
        today["zec_share"] = float(zec_share)
    cleaned.append(today)
    if len(cleaned) < 2 and ch24_f is not None and ch24_f > -100:
        prev = float(mcap) / (1.0 + ch24_f / 100.0)
        if prev > 0:
            prev_day = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            if not any(str(row.get("time") or "") == prev_day for row in cleaned):
                cleaned.append({"time": prev_day, "market_cap": prev})
    cleaned.sort(key=lambda r: str(r["time"]))

    doc["latest_top"] = latest_top
    doc["data"] = cleaned
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    print(f"ok {path} top={len(latest_top)} days={len(cleaned)} latest={day} mcap={mcap}")


def main() -> None:
    paths = [Path(item) for item in sys.argv[1:]]
    if not paths:
        paths = [Path(__file__).resolve().parent / "output" / "json" / "privacy_coins.json"]
    for path in paths:
        attach(path)


if __name__ == "__main__":
    main()
