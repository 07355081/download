"""单次会话快照：Yahoo quote + CEX ticker → tradfi-arb-live.json（覆写，不存档）。"""
from __future__ import annotations

import argparse
import sys
import time

import _paths as P
from _cleanup import purge_legacy_history
from _guard import SpotLock, preflight
from cex_tickers import fetch_lasts, load_focus_instruments
from yahoo import fetch_quotes

sys.path.insert(0, str(P.PARENT / "tradfi"))
from _common import Http  # type: ignore


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-lock", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    P.ensure_dirs()
    lock = SpotLock()
    if not args.no_lock:
        if not lock.acquire():
            print("[skip] tradfi-spot lock busy", flush=True)
            return 0
    try:
        if not args.force:
            g = preflight()
            if not g.ok:
                print(f"[skip] {g.reason}", flush=True)
                return 0

        uni = P.load_universe(refresh=True)
        assets = uni["assets"]
        print(f"dynamic top-{len(assets)}", flush=True)
        focus = {a["base_asset"] for a in assets}
        yahoo_list = [a["yahoo"] for a in assets]
        http = Http(min_interval_s=0.15, timeout=25, retries=2)

        t0 = time.time()
        quotes = fetch_quotes(http, yahoo_list)
        print(f"yahoo quotes={len(quotes)}/{len(yahoo_list)}", flush=True)

        instruments = load_focus_instruments(focus)
        lasts = fetch_lasts(http, instruments)
        print(f"cex lasts={len(lasts)}/{len(instruments)}", flush=True)

        now = int(time.time() * 1000)
        rows = []
        for a in assets:
            base = a["base_asset"]
            ysym = a["yahoo"]
            q = quotes.get(ysym)
            ref_last = q["last"] if q else None
            ref_asof = q["asof_ms"] if q else None
            ref_stale = True
            if ref_asof is not None:
                ref_stale = (now - ref_asof) > P.REF_STALE_MS

            legs = []
            for inst in instruments:
                if inst["base_asset"] != base:
                    continue
                tick = lasts.get((inst["exchange"], inst["instrument_id"]))
                if not tick or ref_last is None or ref_last == 0:
                    continue
                prem = (tick["last"] - ref_last) / ref_last * 100.0
                legs.append({
                    "exchange": inst["exchange"],
                    "instrument": inst["instrument_id"],
                    "last": tick["last"],
                    "asof_ms": tick["asof_ms"],
                    "premium_pct": prem,
                    "source": tick.get("source"),
                })
            if ref_last is None and not legs:
                continue
            best = None
            if legs:
                best = max(legs, key=lambda x: abs(x["premium_pct"]))["premium_pct"]
            ages = []
            if ref_asof is not None:
                ages.append(now - ref_asof)
            ages.extend(now - L["asof_ms"] for L in legs if L.get("asof_ms"))
            rows.append({
                "base_asset": base,
                "sector": a["sector"],
                "ref": {
                    "venue": a["venue"],
                    "symbol": ysym,
                    "last": ref_last,
                    "asof_ms": ref_asof,
                },
                "legs": legs,
                "best_premium_pct": best,
                "age_ms": max(ages) if ages else None,
                "ref_stale": ref_stale if ref_last is not None else True,
            })

        rows.sort(key=lambda r: abs(r["best_premium_pct"] or 0), reverse=True)
        payload = {
            "asof_ms": now,
            "schedule_sec": uni.get("schedule_sec", P.SCHEDULE_SEC),
            "source_ref": "yahoo",
            "rows": rows,
        }
        P.atomic_write_json(P.LIVE_PATH, payload)
        purged = purge_legacy_history()
        purged_n = sum(purged.values())
        print(
            f"wrote {P.LIVE_PATH} rows={len(rows)} "
            f"purged_legacy={purged_n} elapsed={time.time()-t0:.1f}s",
            flush=True,
        )
        return 0
    finally:
        if not args.no_lock:
            lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
