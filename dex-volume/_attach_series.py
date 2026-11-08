# -*- coding: utf-8 -*-
"""给已有 dex_volume.json 补 series，让飞书页能画图。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def pct_change(curr: float, prev: float) -> float | None:
    if prev == 0:
        return None
    return (curr / prev - 1.0) * 100.0


def chart_series(data: list[dict[str, Any]], key: str, label: str, label_en: str) -> dict[str, Any] | None:
    months: list[dict[str, Any]] = []
    prev: float | None = None
    for row in data:
        day = row.get("time")
        val = row.get(key)
        if not isinstance(day, str) or not isinstance(val, (int, float)):
            continue
        mom = pct_change(float(val), prev) if prev is not None else None
        months.append(
            {
                "month": day,
                "usd": float(val),
                "mom_pct": None if mom is None else round(mom, 4),
            }
        )
        prev = float(val)
    if len(months) < 2:
        return None
    return {"label": label, "labelEn": label_en, "months": months}


def attach(path: Path) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    data = doc.get("data")
    if not isinstance(doc, dict) or not isinstance(data, list):
        raise SystemExit(f"bad json {path}")
    blocks = [
        block
        for block in (
            chart_series(data, "global", "全球 DEX 现货", "Global DEX spot"),
            chart_series(data, "solana", "Solana", "Solana"),
            chart_series(data, "ethereum", "Ethereum", "Ethereum"),
        )
        if block
    ]
    doc["series"] = blocks
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    print(f"ok {path} series={len(blocks)} latest={doc.get('latest_day')} global={doc.get('latest_global')}")


def main() -> None:
    paths = [Path(item) for item in sys.argv[1:]]
    if not paths:
        paths = [Path(__file__).resolve().parent / "output" / "json" / "dex_volume.json"]
    for path in paths:
        attach(path)


if __name__ == "__main__":
    main()
