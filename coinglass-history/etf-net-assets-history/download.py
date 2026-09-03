"""Download Coinglass /api/etf/{market}/net-assets/history (per-market 总净资产 K线).

Endpoint:  GET /api/etf/{market}/net-assets/history
Supported markets: bitcoin ✓, ethereum ✓  (solana/xrp 404)

Response:  list of {timestamp, net_assets_usd, change_usd, price_usd}

Cache mapping (OHLC-schema abuse):
    open  = price_usd       (BTC/ETH 价格作为开盘参考)
    close = net_assets_usd  (净资产主曲线)
    extra_json = {change_usd}
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

SUPPORTED_MARKETS = ("bitcoin", "ethereum")


def extract_rows(data) -> list[dict]:
    out = []
    if not isinstance(data, list):
        return out
    for item in data:
        if not isinstance(item, dict): continue
        ts_ms = C.normalize_ts_ms(item.get("timestamp"))
        if ts_ms is None: continue
        row = {"time_ms": ts_ms}
        extra = {}
        # Map fields
        v = item.get("price_usd")
        if v not in (None, ""):
            try: row["open"] = float(v)
            except (ValueError, TypeError): pass
        v = item.get("net_assets_usd")
        if v not in (None, ""):
            try: row["close"] = float(v)
            except (ValueError, TypeError): pass
        v = item.get("change_usd")
        if v not in (None, ""):
            try: extra["change_usd"] = float(v)
            except (ValueError, TypeError): pass
        row["extra_json"] = json.dumps(extra, ensure_ascii=False, separators=(",", ":")) if extra else ""
        out.append(row)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--market", default=None, help=f"Subset of {SUPPORTED_MARKETS}")
    p.add_argument("--force", action="store_true")
    p.add_argument("--recheck-days", type=int, default=C.ETF_RECHECK_DAYS)
    p.add_argument("--retry", type=int, default=3); p.add_argument("--sleep", type=float, default=0.5)
    p.add_argument("--api-key", default=None)
    args = p.parse_args()
    api_key = C.resolve_api_key(args.api_key)
    CACHE_DIR.mkdir(parents=True, exist_ok=True); OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    markets = list(SUPPORTED_MARKETS) if not args.market else [m.strip() for m in args.market.split(",")]
    status_rows = []; failures = 0; skipped = 0
    for idx, market in enumerate(markets, 1):
        cf = CACHE_DIR / f"{market}.csv"
        cached = {} if args.force else C.read_ohlc_cache(cf)
        emax = C.latest_ohlc_ts_ms(cached)
        latest_date = C.ts_to_date(emax) if emax else ""
        row = {"index": idx, "exchange": "", "symbol": market, "interval": "net-assets", "status_code": 0}

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
                f"[{idx}/{len(markets)}] {market}  skipped fresh  "
                f"(cached {len(cached)}, latest {latest_date})",
                flush=True,
            )
            continue

        url = f"{C.API_BASE}/api/etf/{market}/net-assets/history"
        status, payload = C.http_get_json(url, api_key=api_key, retries=args.retry)
        row["status_code"] = status
        if status != 200 or not isinstance(payload, dict) or str(payload.get("code")) != "0":
            failures += 1
            err = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)[:300]
            row.update({"ok": False, "cache_total": len(cached), "error_body": err})
            status_rows.append(row); print(f"[{idx}/{len(markets)}] {market}  FAIL"); time.sleep(args.sleep); continue
        rows = extract_rows(payload.get("data") or [])
        merged, added = C.merge_ohlc_rows(cached, rows, 2, emax)
        if merged: C.write_ohlc_cache(cf, merged)
        latest_ms = max(merged.keys()) if merged else None
        row.update({"ok": True, "rows_added": added, "cache_total": len(merged),
                    "latest_date": C.ts_to_date(latest_ms) if latest_ms else ""})
        status_rows.append(row)
        print(f"[{idx}/{len(markets)}] {market}  +{added} new  (cached {len(merged)}, latest {row['latest_date']})")
        time.sleep(args.sleep)
    C.write_status_csv(STATUS_CSV, status_rows)
    print(f"\nDONE. failed={failures}  skipped_fresh={skipped}")
    if failures: sys.exit(2)


if __name__ == "__main__":
    main()

