"""Download Coinglass /api/etf/{market}/flow-history (per-market net flow time series).

Endpoint:  GET /api/etf/{market}/flow-history?range=all
Markets:   bitcoin, ethereum, solana, xrp
Cache:     cache/<market>.csv  (OHLC schema; flow_usd / per-ETF flows in extra_json)
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _common as C  # noqa: E402

HERE = C.module_dir(__file__)
CACHE_DIR = HERE / "cache"
OUTPUT_DIR = HERE / "output"
STATUS_CSV = OUTPUT_DIR / "_run_status.csv"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--market", default=None)
    p.add_argument("--force", action="store_true")
    p.add_argument("--recheck-days", type=int, default=C.ETF_RECHECK_DAYS)
    p.add_argument("--retry", type=int, default=3); p.add_argument("--sleep", type=float, default=0.5)
    p.add_argument("--api-key", default=None)
    args = p.parse_args()
    api_key = C.resolve_api_key(args.api_key)
    CACHE_DIR.mkdir(parents=True, exist_ok=True); OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    markets = list(C.ETF_MARKETS) if not args.market else [m.strip() for m in args.market.split(",")]
    status_rows = []; failures = 0; skipped = 0
    for idx, market in enumerate(markets, 1):
        cf = CACHE_DIR / f"{market}.csv"
        cached = {} if args.force else C.read_ohlc_cache(cf)
        emax = C.latest_ohlc_ts_ms(cached)
        latest_date = C.ts_to_date(emax) if emax else ""
        row = {"index": idx, "exchange": "", "symbol": market, "interval": "flow", "status_code": 0}

        if C.should_skip_api_fetch(emax, "1d", force=args.force, recheck_days=args.recheck_days):
            skipped += 1
            row.update({
                "ok": True,
                "rows_added": 0,
                "cache_total": len(cached),
                "latest_date": latest_date,
                "skipped_fresh": True,
            })
            status_rows.append(row)
            print(
                f"[{idx}/{len(markets)}] {market} flow-history  skipped fresh  "
                f"(cached {len(cached)}, latest {latest_date})",
                flush=True,
            )
            continue

        status, payload = C.fetch_etf_endpoint(market, "flow-history", {"range": "all"},
                                                api_key=api_key, retries=args.retry)
        row["status_code"] = status
        if status != 200 or not isinstance(payload, dict) or str(payload.get("code")) != "0":
            failures += 1
            err = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)[:300]
            row.update({"ok": False, "cache_total": len(cached), "error_body": err})
            status_rows.append(row); print(f"[{idx}/{len(markets)}] {market} flow-history  FAIL"); time.sleep(args.sleep); continue
        rows = C.extract_ohlc_rows(payload.get("data") or [])
        merged, added = C.merge_ohlc_rows(cached, rows, 2, emax)
        if merged: C.write_ohlc_cache(cf, merged)
        latest_ms = max(merged.keys()) if merged else None
        row.update({"ok": True, "rows_added": added, "cache_total": len(merged),
                    "latest_date": C.ts_to_date(latest_ms) if latest_ms else ""})
        status_rows.append(row)
        print(f"[{idx}/{len(markets)}] {market} flow-history  +{added} new  (cached {len(merged)}, latest {row['latest_date']})")
        time.sleep(args.sleep)
    C.write_status_csv(STATUS_CSV, status_rows)
    print(f"\nDONE. failed={failures}  skipped_fresh={skipped}")
    if failures: sys.exit(2)


if __name__ == "__main__":
    main()

