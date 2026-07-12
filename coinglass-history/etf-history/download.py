"""Download Coinglass /api/etf/{market}/history (per-ticker historical data).

Endpoint:  GET /api/etf/{market}/history?ticker=&range=all
Markets:   bitcoin, ethereum, solana, xrp
Tickers:   loaded from ../etf-list/cache/<market>.json — needs etf-list to run first.

Response shape (non-OHLC; custom extractor below):
    [{ "market_date": <ms>, "assets_date": <ms>,
       "market_price": <usd>, "nav": <usd>, "net_assets": <usd>,
       "btc_holdings"|"eth_holdings"|...: <coin>,
       "premium_discount": <pct>, "shares_outstanding": <int>,
       "name": <str>, "ticker": <str> }, ...]

Cache:     cache/<market>_<ticker>.csv  (OHLC schema abused:
             close = market_price   (main price series)
             open  = nav             (for premium/discount comparison)
             all other fields preserved in extra_json)
Output:    output/_run_status.csv

Usage:  python download.py [--market bitcoin] [--ticker IBIT,FBTC] [--force]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _common as C  # noqa: E402


def extract_etf_rows(data) -> list[dict]:
    """Custom ETF-history extractor. Time key is market_date (fallback assets_date);
    close=market_price, open=nav; everything else stored in extra_json.
    """
    out: list[dict] = []
    if not isinstance(data, list):
        return out
    for item in data:
        if not isinstance(item, dict):
            continue
        ts_ms = None
        for k in ("market_date", "assets_date"):
            ts_ms = C.normalize_ts_ms(item.get(k))
            if ts_ms is not None:
                break
        if ts_ms is None:
            continue
        row: dict = {"time_ms": ts_ms}
        extra: dict = {}
        for key, dst in (("market_price", "close"), ("nav", "open")):
            v = item.get(key)
            if v is None or v == "":
                continue
            try:
                row[dst] = float(v)
            except (ValueError, TypeError):
                pass
        for k, v in item.items():
            if k in ("market_date", "assets_date", "market_price", "nav"):
                continue
            if v is None or v == "":
                continue
            if isinstance(v, bool):
                extra[k] = v
            elif isinstance(v, (int, float)):
                extra[k] = v
            elif isinstance(v, str):
                extra[k] = v
        row["extra_json"] = json.dumps(extra, ensure_ascii=False, separators=(",", ":")) if extra else ""
        out.append(row)
    return out

HERE = C.module_dir(__file__)
CACHE_DIR = HERE / "cache"
OUTPUT_DIR = HERE / "output"
STATUS_CSV = OUTPUT_DIR / "_run_status.csv"
LIST_CACHE_DIR = HERE.parent / "etf-list" / "cache"


def list_tickers(market: str) -> list[str]:
    p = LIST_CACHE_DIR / f"{market}.json"
    if not p.exists():
        return []
    items = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        return []
    return [it.get("ticker") for it in items if isinstance(it, dict) and it.get("ticker")]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--market", default=None, help=f"Subset of {C.ETF_MARKETS}")
    p.add_argument("--ticker", default=None, help="Comma-separated tickers")
    p.add_argument("--force", action="store_true")
    p.add_argument("--recheck-days", type=int, default=C.ETF_RECHECK_DAYS)
    p.add_argument("--retry", type=int, default=3); p.add_argument("--sleep", type=float, default=0.3)
    p.add_argument("--max-failures", type=int, default=20); p.add_argument("--max-tuples", type=int, default=0)
    p.add_argument("--api-key", default=None)
    args = p.parse_args()

    api_key = C.resolve_api_key(args.api_key)
    CACHE_DIR.mkdir(parents=True, exist_ok=True); OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Coinglass v4 only exposes /history for bitcoin (ethereum/solana/xrp 404).
    SUPPORTED = ("bitcoin",)
    markets = list(SUPPORTED) if not args.market else [m.strip() for m in args.market.split(",")]
    t_filter = set(t.strip() for t in args.ticker.split(",")) if args.ticker else None

    tuples: list[tuple[str, str]] = []
    for market in markets:
        for tk in list_tickers(market):
            if t_filter and tk not in t_filter: continue
            tuples.append((market, tk))
    if args.max_tuples and args.max_tuples > 0: tuples = tuples[: args.max_tuples]
    if not tuples:
        sys.exit("No tickers found. Run etf-list/download.py first.")
    print(f"endpoint=/api/etf/<market>/history  tuples={len(tuples)}  cache={CACHE_DIR}")

    status_rows = []; failures = 0; new_n = 0; skipped = 0
    for idx, (market, ticker) in enumerate(tuples, 1):
        cf = CACHE_DIR / f"{C.safe_filename(market, ticker)}.csv"
        cached = {} if args.force else C.read_ohlc_cache(cf)
        emax = C.latest_ohlc_ts_ms(cached)
        latest_date = C.ts_to_date(emax) if emax else ""
        row = {
            "index": idx,
            "exchange": "",
            "symbol": f"{market}/{ticker}",
            "interval": "1d",
            "status_code": 0,
        }

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
                f"[{idx:04d}/{len(tuples)}] {market}/{ticker}  skipped fresh  "
                f"(cached {len(cached)}, latest {latest_date})",
                flush=True,
            )
            continue

        status, payload = C.fetch_etf_endpoint(market, "history", {"ticker": ticker, "range": "all"},
                                                api_key=api_key, retries=args.retry)
        row["status_code"] = status
        if status != 200 or not isinstance(payload, dict) or str(payload.get("code")) != "0":
            failures += 1
            err = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)[:300]
            row.update({"ok": False, "cache_total": len(cached), "error_body": err})
            status_rows.append(row); C.write_status_csv(STATUS_CSV, status_rows)
            print(f"[{idx:04d}/{len(tuples)}] {market}/{ticker}  FAIL status={status}")
            if failures > args.max_failures:
                print(f"\nSTOPPED: failures={failures} > limit={args.max_failures}"); break
            time.sleep(args.sleep); continue

        rows = extract_etf_rows(payload.get("data") or [])
        if not rows and not cached:
            row.update({"ok": True, "rows_added": 0, "cache_total": 0, "latest_date": ""})
            status_rows.append(row); time.sleep(args.sleep); continue

        merged, added = C.merge_ohlc_rows(cached, rows, 2, emax)
        C.write_ohlc_cache(cf, merged)
        latest_ms = max(merged.keys()) if merged else None
        latest_date = C.ts_to_date(latest_ms) if latest_ms else ""
        row.update({"ok": True, "rows_added": added, "cache_total": len(merged), "latest_date": latest_date})
        status_rows.append(row)
        if added == 0 and emax is not None:
            skipped += 1
            print(f"[{idx:04d}/{len(tuples)}] {market}/{ticker}  up-to-date  (latest {latest_date})")
        else:
            new_n += 1
            print(f"[{idx:04d}/{len(tuples)}] {market}/{ticker}  +{added} new  (cached {len(merged)}, latest {latest_date})")
        if idx % 20 == 0: C.write_status_csv(STATUS_CSV, status_rows)
        time.sleep(args.sleep)

    C.write_status_csv(STATUS_CSV, status_rows)
    print(f"\nDONE.  fetched={new_n}  skipped_fresh={skipped}  failed={failures}")
    print(f"       status -> {STATUS_CSV}\n       cache  -> {CACHE_DIR}")
    if failures: sys.exit(2)


if __name__ == "__main__":
    main()

