# -*- coding: utf-8 -*-
"""CEX 合约（永续为主）日成交量汇总序列。

DefiLlama overview/derivatives 免费接口 402（Pro），故本模块从已有
cex-asset&vol 的日合约成交 JSON 派生全球 ALL 序列，并算周环比。

口径：
  - CoinGecko → 自家洗量调整后的 CEX 合约日成交（含 Hyperliquid 等）
  - 不是 DefiLlama 链上 perp 全集；与 Lookonchain 永续 WoW 可能不一致

输出：
  output/json/perp_volume.json

用法：
  python download.py
"""
from __future__ import annotations

import json
import sys
from calendar import monthrange
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_DIR = HERE / "output" / "json"
OUT_PATH = JSON_DIR / "perp_volume.json"

CEX_CANDIDATES = [
    ROOT / "cex-asset&vol" / "output" / "json" / "cex_exchange_daily_futures_volume_usd.json",
    Path("/root/data-download/cex-asset&vol/output/json/cex_exchange_daily_futures_volume_usd.json"),
    Path("/root/dashboard/public/json/cex/cex_exchange_daily_futures_volume_usd.json"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_cex_source() -> Path:
    for p in CEX_CANDIDATES:
        if p.is_file():
            return p
    raise FileNotFoundError(
        "缺少 cex_exchange_daily_futures_volume_usd.json；"
        "请先跑 cex-asset&vol/run_all.py"
    )


def pct_change(curr: float, prev: float) -> float | None:
    if prev == 0:
        return None
    return (curr / prev - 1.0) * 100.0


def week_ending_sunday(day: str) -> str:
    d = datetime.strptime(day, "%Y-%m-%d").date()
    sun = d + timedelta(days=(6 - d.weekday()))
    return sun.isoformat()


def build_weeks(day_map: dict[str, float]) -> list[dict[str, Any]]:
    by_w: dict[str, float] = defaultdict(float)
    days_in_w: dict[str, set[str]] = defaultdict(set)
    for day, val in day_map.items():
        w = week_ending_sunday(day)
        by_w[w] += val
        days_in_w[w].add(day)
    rows: list[dict[str, Any]] = []
    prev: float | None = None
    for w in sorted(by_w):
        if len(days_in_w[w]) < 5:
            continue
        total = by_w[w]
        wow = pct_change(total, prev) if prev is not None else None
        rows.append(
            {
                "week_end": w,
                "volume_usd": round(total, 2),
                "days": len(days_in_w[w]),
                "wow_pct": None if wow is None else round(wow, 4),
            }
        )
        prev = total
    return rows


def complete_months(day_map: dict[str, float]) -> list[dict[str, Any]]:
    by_m: dict[str, float] = defaultdict(float)
    days_in_m: dict[str, set[str]] = defaultdict(set)
    for day, val in day_map.items():
        ym = day[:7]
        by_m[ym] += val
        days_in_m[ym].add(day)
    rows: list[dict[str, Any]] = []
    prev: float | None = None
    for ym in sorted(by_m):
        y, m = int(ym[:4]), int(ym[5:7])
        need = monthrange(y, m)[1]
        if len(days_in_m[ym]) < need:
            continue
        total = by_m[ym]
        mom = pct_change(total, prev) if prev is not None else None
        rows.append(
            {
                "month": ym,
                "volume_usd": round(total, 2),
                "mom_pct": None if mom is None else round(mom, 4),
            }
        )
        prev = total
    return rows


def load_day_map(src: Path) -> tuple[dict[str, float], list[str], str]:
    doc = json.loads(src.read_text(encoding="utf-8"))
    data = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError(f"unexpected cex json shape: {src}")
    rows = data.get("rows")
    exchanges = data.get("exchanges") or []
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"empty cex futures rows: {src}")
    day_map: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = str(row.get("date") or "")
        if len(day) != 10:
            continue
        all_v = row.get("ALL")
        if isinstance(all_v, (int, float)):
            day_map[day] = float(all_v)
            continue
        # fallback: sum numeric exchange cols
        total = 0.0
        any_n = False
        for k, v in row.items():
            if k in ("date", "ALL"):
                continue
            if isinstance(v, (int, float)):
                total += float(v)
                any_n = True
        if any_n:
            day_map[day] = total
    gen = str(data.get("generated_at") or doc.get("updated_at") or "")
    ex_names = [str(x) for x in exchanges] if isinstance(exchanges, list) else []
    return day_map, ex_names, gen


def main() -> int:
    try:
        src = find_cex_source()
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    print(f"[perp-volume] source={src}")
    day_map, exchanges, src_gen = load_day_map(src)
    days = sorted(day_map)
    data = [{"time": d, "global": day_map[d]} for d in days]
    weeks = build_weeks(day_map)
    months = complete_months(day_map)
    latest = data[-1] if data else {}

    payload = {
        "code": "0",
        "msg": "success",
        "name": "perp_volume",
        "description": "CEX 合约日成交合计（永续为主，派生自 cex-asset&vol）",
        "source": "cex-asset&vol",
        "source_file": str(src),
        "source_generated_at": src_gen,
        "source_url": "https://www.coingecko.com/",
        "metric": "cex_futures_daily_volume_usd",
        "note": (
            "DefiLlama overview/derivatives 需 Pro（402）；本序列用 CoinGecko→洗量后的 "
            "CEX 合约日成交 ALL。与 Lookonchain「永续成交量」可能不一致，发稿须写明 "
            "CEX 合约样本口径，勿标 Lookonchain / DefiLlama derivatives 同源。"
            "周标签为 UTC 自然周周日。"
        ),
        "updated_at": now_iso(),
        "exchanges": exchanges,
        "row_count": len(data),
        "latest_day": latest.get("time"),
        "latest_global": latest.get("global"),
        "weeks": weeks[-52:],
        "months": months[-36:],
        "data": data,
    }

    JSON_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(OUT_PATH)

    last_w = weeks[-1] if weeks else None
    print(
        f"[perp-volume] wrote {OUT_PATH} rows={len(data)} "
        f"latest={latest.get('time')} global={latest.get('global')} "
        f"last_week={last_w}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
