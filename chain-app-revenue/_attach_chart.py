# -*- coding: utf-8 -*-
"""给已有 chain_app_revenue_latest.json 补 latest_top / series，让飞书页能画图。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

KEEP = 180


def display_name(chain_id: str) -> str:
    return chain_id.replace("_", " ").title()


def latest_top_from(top: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in top:
        if not isinstance(row, dict):
            continue
        usd = row.get("revenue_usd")
        if not isinstance(usd, (int, float)):
            continue
        name = str(row.get("name") or row.get("id") or "")
        if not name:
            continue
        out.append({"symbol": name, "name": name, "usd": float(usd)})
    return out


def build_series(history_dir: Path, chain_ids: list[str]) -> list[dict[str, Any]]:
    files = sorted(history_dir.glob("????-??-??.json"))[-KEEP:]
    buckets: dict[str, list[dict[str, Any]]] = {cid: [] for cid in chain_ids}
    prev: dict[str, float | None] = {cid: None for cid in chain_ids}
    labels: dict[str, str] = {}
    for path in files:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(doc, dict):
            continue
        day = str(doc.get("day") or path.stem)
        by_id: dict[str, dict[str, Any]] = {}
        for row in list(doc.get("top") or []) + list(doc.get("chains") or []):
            if isinstance(row, dict) and row.get("id"):
                by_id[str(row["id"])] = row
        for cid in chain_ids:
            row = by_id.get(cid)
            if not row:
                continue
            val = row.get("revenue_usd")
            if not isinstance(val, (int, float)):
                continue
            last = prev[cid]
            mom = None if last in (None, 0) else round((float(val) / float(last) - 1.0) * 100.0, 4)
            buckets[cid].append({"month": day, "usd": float(val), "mom_pct": mom})
            prev[cid] = float(val)
            labels[cid] = str(row.get("name") or display_name(cid))
    out: list[dict[str, Any]] = []
    for cid in chain_ids:
        months = buckets[cid]
        if len(months) < 2:
            continue
        name = labels.get(cid) or display_name(cid)
        out.append({"label": name, "labelEn": name, "months": months})
    return out


def attach(latest: Path, history_dir: Path) -> None:
    doc = json.loads(latest.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise SystemExit(f"bad json {latest}")
    top = doc.get("top") if isinstance(doc.get("top"), list) else []
    chain_ids = [str(row["id"]) for row in top[:5] if isinstance(row, dict) and row.get("id")]
    doc["latest_top"] = latest_top_from(top)
    doc["series"] = build_series(history_dir, chain_ids)
    tmp = latest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(latest)
    print(
        f"ok {latest} top={len(doc['latest_top'])} series={len(doc['series'])} "
        f"days={[len(b.get('months') or []) for b in doc['series']]} "
        f"latest={doc.get('day')}"
    )


def main() -> None:
    if len(sys.argv) >= 3:
        latest = Path(sys.argv[1])
        history = Path(sys.argv[2])
        attach(latest, history)
        return
    here = Path(__file__).resolve().parent
    attach(here / "output" / "json" / "chain_app_revenue_latest.json", here / "output" / "json" / "chain_app_revenue_history")


if __name__ == "__main__":
    main()
