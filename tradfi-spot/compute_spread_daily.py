"""[已废弃] 用传统参考价 1d + 本地 tradfi-price 1d 计算日线结构性基差。

live-only 模式不再调用；保留脚本仅供回溯。请用 snapshot_once.py。
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict

import _paths as P
from _guard import SpotLock, preflight
from cex_tickers import load_focus_instruments

sys.path.insert(0, str(P.PARENT / "tradfi"))
from _common import read_rows, to_num  # type: ignore


def _day_key(ms: int) -> int:
    return int(ms // 86_400_000 * 86_400_000)


def _load_spot_series(base: str, venue: str) -> dict[int, float]:
    path = P.spot_price_file(venue, base, "1d")
    out: dict[int, float] = {}
    for r in read_rows(path):
        tm = r.get("time")
        c = to_num(r.get("close"))
        if isinstance(tm, (int, float)) and c is not None:
            out[_day_key(int(tm))] = c
    return out


def _load_cex_series(exchange: str, instrument_id: str) -> dict[int, float]:
    path = P.TRADFI_PRICE_DIR / f"{P.safe_seg(exchange)}_{P.safe_seg(instrument_id)}_1d.json"
    out: dict[int, float] = {}
    for r in read_rows(path):
        tm = r.get("time")
        c = to_num(r.get("close"))
        if isinstance(tm, (int, float)) and c is not None:
            out[_day_key(int(tm))] = c
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-lock", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--after-tradfi", action="store_true")
    args = ap.parse_args()

    P.ensure_dirs()
    lock = SpotLock()
    if not args.no_lock:
        if not lock.acquire():
            print("[skip] tradfi-spot lock busy", flush=True)
            return 0
    try:
        if not args.force:
            g = preflight(ignore_tradfi_lock=args.after_tradfi)
            if not g.ok:
                print(f"[skip] {g.reason}", flush=True)
                return 0

        uni = P.load_universe_cached()
        assets = {a["base_asset"]: a for a in uni["assets"]}
        focus = set(assets)
        instruments = load_focus_instruments(focus)
        by_base: dict[str, list[dict]] = defaultdict(list)
        for inst in instruments:
            by_base[inst["base_asset"]].append(inst)

        n = 0
        for base, meta in assets.items():
            spot = _load_spot_series(base, meta["venue"])
            if not spot:
                print(f"  SKIP {base}: no spot series", flush=True)
                continue
            legs_meta = by_base.get(base) or []
            cex_series: dict[str, dict[int, float]] = {}
            inst_by_ex: dict[str, str] = {}
            for inst in legs_meta:
                ex = inst["exchange"]
                iid = inst["instrument_id"]
                series = _load_cex_series(ex, iid)
                if series:
                    cex_series[ex] = series
                    inst_by_ex[ex] = iid

            if not cex_series:
                print(f"  SKIP {base}: no cex 1d", flush=True)
                continue

            data = []
            for day in sorted(spot.keys()):
                ref = spot[day]
                if not ref:
                    continue
                legs = {}
                for ex, series in cex_series.items():
                    c = series.get(day)
                    if c is None:
                        continue
                    legs[ex] = {
                        "instrument": inst_by_ex[ex],
                        "close": c,
                        "premium_pct": (c - ref) / ref * 100.0,
                    }
                if not legs:
                    continue
                data.append({"time": day, "ref_close": ref, "ref_stale": False, "legs": legs})

            if len(data) > P.MAX_DAILY_ROWS:
                data = data[-P.MAX_DAILY_ROWS:]

            latest = None
            if data:
                last = data[-1]
                latest = {
                    "time": last["time"],
                    "ref_close": last["ref_close"],
                    "legs": [{"exchange": ex, **v} for ex, v in last["legs"].items()],
                }

            payload = {
                "base_asset": base,
                "sector": meta["sector"],
                "interval": "1d",
                "ref": {"venue": meta["venue"], "symbol": meta["yahoo"], "source": "yahoo"},
                "exchanges": sorted(cex_series.keys()),
                "latest": latest,
                "data": data,
            }
            P.atomic_write_json(P.arb_spread_file(base, "1d"), payload)
            n += 1
            print(f"  OK {base:<8} days={len(data)} exchanges={len(cex_series)}", flush=True)

        print(f"done spreads={n}", flush=True)
        return 0 if n else 1
    finally:
        if not args.no_lock:
            lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
