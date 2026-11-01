"""cache/<market>.csv -> output/json/<market>.json (+ <market>_flow_by_ticker.json).

CoinGlass /api/etf/{market}/flow-history returns per-day `etf_flows` (IBIT/FBTC/…).
Older caches dropped that array; after a --force re-download, extra_json keeps it and
this script also emits the long-form ticker file the dashboard expects.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import _common as C  # noqa: E402
import _json_merge as JM  # noqa: E402

HERE = C.module_dir(__file__)
CACHE_DIR = HERE / "cache"
JSON_OUT_DIR = HERE / "output" / "json"


def _candle_from_row(ts: int, r: dict) -> dict:
    c: dict = {"time": ts}
    for k in ("open", "high", "low", "close", "volume_usd"):
        v = r.get(k)
        if v not in (None, ""):
            try:
                c[k] = float(v)
            except (ValueError, TypeError):
                pass
    extra = r.get("extra_json") or ""
    if extra:
        try:
            eo = json.loads(extra)
            if isinstance(eo, dict):
                for k, v in eo.items():
                    c.setdefault(k, v)
        except json.JSONDecodeError:
            pass
    return c


def _ticker_rows_from_candles(candles: list[dict]) -> list[dict]:
    out: list[dict] = []
    for c in candles:
        ts = c.get("time")
        flows = c.get("etf_flows")
        if not isinstance(ts, int) or not isinstance(flows, list):
            continue
        for item in flows:
            if not isinstance(item, dict):
                continue
            ticker = str(item.get("etf_ticker") or item.get("ticker") or "").strip()
            if not ticker:
                continue
            try:
                flow = float(item.get("flow_usd"))
            except (TypeError, ValueError):
                continue
            out.append({"time": ts, "etf_ticker": ticker, "flow_usd": flow})
    out.sort(key=lambda r: (r["time"], r["etf_ticker"]))
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--market", default=None)
    args = p.parse_args()
    if not CACHE_DIR.exists():
        raise SystemExit(f"cache dir not found: {CACHE_DIR}")
    JSON_OUT_DIR.mkdir(parents=True, exist_ok=True)
    m_f = set(m.strip() for m in args.market.split(",")) if args.market else None
    written = 0
    ticker_written = 0
    for cf in sorted(CACHE_DIR.glob("*.csv")):
        market = cf.stem
        if m_f and market not in m_f:
            continue
        rows = C.read_ohlc_cache(cf)
        if not rows:
            continue
        candles = [_candle_from_row(ts, rows[ts]) for ts in sorted(rows.keys())]
        payload = {"code": "0", "msg": "success", "market": market, "data": candles}
        out = JSON_OUT_DIR / f"{market}.json"
        JM.write_coinglass_candles_json(out, payload)
        print(f"  wrote {out.name}  ({len(candles)} rows, {out.stat().st_size:,} bytes)")
        written += 1

        ticker_rows = _ticker_rows_from_candles(candles)
        ticker_out = JSON_OUT_DIR / f"{market}_flow_by_ticker.json"
        if ticker_rows:
            ticker_payload = {
                "code": "0",
                "msg": "success",
                "market": market,
                "data": ticker_rows,
            }
            JM.write_coinglass_candles_json(ticker_out, ticker_payload)
            print(
                f"  wrote {ticker_out.name}  ({len(ticker_rows)} rows, "
                f"{ticker_out.stat().st_size:,} bytes)"
            )
            ticker_written += 1
        elif ticker_out.exists():
            # Stale file from a previous richer cache — keep until a force refresh lands.
            print(f"  skip {ticker_out.name}  (no etf_flows in cache; leave existing file)")
    print(f"\nDONE. wrote={written} ticker_files={ticker_written} -> {JSON_OUT_DIR}")
