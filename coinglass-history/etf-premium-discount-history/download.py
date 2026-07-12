"""Download Coinglass /api/etf/bitcoin/premium-discount/history
(BTC ETF 各个 ticker 的溢价/折价历史).

Endpoint:  GET /api/etf/bitcoin/premium-discount/history
Response:  list of {timestamp, list: [{ticker, nav_usd, market_price_usd, premium_discount_details}]}

Only bitcoin is supported (ethereum/solana/xrp variants return 404).

Cache: long format (time_ms, key, value) where
    key = f"{ticker}__{field}" e.g. "IBIT__premium_discount_details"
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _common as C  # noqa: E402

HERE = C.module_dir(__file__)
CACHE_DIR = HERE / "cache"
OUTPUT_DIR = HERE / "output"
STATUS_CSV = OUTPUT_DIR / "_run_status.csv"
PRESERVED = ("nav_usd", "market_price_usd", "premium_discount_details")


def extract_entries(data) -> dict[tuple[int, str], float]:
    out: dict[tuple[int, str], float] = {}
    if not isinstance(data, list):
        return out
    for item in data:
        if not isinstance(item, dict): continue
        ts = C.normalize_ts_ms(item.get("timestamp"))
        if ts is None: continue
        sub = item.get("list") or []
        if not isinstance(sub, list): continue
        for row in sub:
            if not isinstance(row, dict): continue
            ticker = str(row.get("ticker") or "").strip()
            if not ticker: continue
            for field in PRESERVED:
                v = row.get(field)
                if v is None or v == "": continue
                try: out[(ts, f"{ticker}__{field}")] = float(v)
                except (ValueError, TypeError): pass
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--force", action="store_true")
    p.add_argument("--recheck-days", type=int, default=C.ETF_RECHECK_DAYS)
    p.add_argument("--retry", type=int, default=3); p.add_argument("--sleep", type=float, default=0.5)
    p.add_argument("--api-key", default=None)
    args = p.parse_args()
    api_key = C.resolve_api_key(args.api_key)
    CACHE_DIR.mkdir(parents=True, exist_ok=True); OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    market = "bitcoin"
    cf = CACHE_DIR / f"{market}.csv"
    cached = {} if args.force else C.read_wide_cache(cf)
    emax = C.latest_wide_ts_ms(cached)
    latest_date = C.ts_to_date(emax) if emax else ""
    row = {"index": 1, "exchange": "", "symbol": market, "interval": "premium-discount", "status_code": 0}

    if C.should_skip_api_fetch(emax, "1d", force=args.force, recheck_days=args.recheck_days):
        row.update({
            "ok": True,
            "rows_added": 0,
            "cache_total": len(cached),
            "latest_date": latest_date,
            "skipped_fresh": True,
        })
        C.write_status_csv(STATUS_CSV, [row])
        print(
            f"[1/1] {market}  skipped fresh  (cached {len(cached)}, latest {latest_date})",
            flush=True,
        )
        return

    url = f"{C.API_BASE}/api/etf/{market}/premium-discount/history"
    status, payload = C.http_get_json(url, api_key=api_key, retries=args.retry)
    row["status_code"] = status
    if status != 200 or not isinstance(payload, dict) or str(payload.get("code")) != "0":
        err = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)[:300]
        row.update({"ok": False, "cache_total": len(cached), "error_body": err})
        C.write_status_csv(STATUS_CSV, [row])
        print(f"[1/1] {market}  FAIL status={status}"); sys.exit(2)

    new_entries = extract_entries(payload.get("data") or [])
    merged = dict(cached); merged.update(new_entries)
    if merged: C.write_wide_cache(cf, merged)
    latest = max((t for (t, _) in merged.keys()), default=None)
    row.update({"ok": True, "rows_added": len(new_entries), "cache_total": len(merged),
                "latest_date": C.ts_to_date(latest) if latest else ""})
    C.write_status_csv(STATUS_CSV, [row])
    n_times = len({t for (t, _) in new_entries.keys()})
    n_tickers = len({k.split("__")[0] for (_, k) in new_entries.keys()})
    print(f"[1/1] {market}  fetched {n_times} timestamps × {n_tickers} tickers → +{len(new_entries)} entries  (cache total: {len(merged)})")


if __name__ == "__main__":
    main()

