"""[已废弃] 下载 focus 标的传统参考价日线（Yahoo）→ tradfi-spot-price/*_1d.json。

live-only 模式不再调用；保留脚本仅供回溯。请用 snapshot_once.py。
"""
from __future__ import annotations

import argparse
import sys
import time

import _paths as P
from _guard import SpotLock, preflight
from yahoo import fetch_daily_bars

sys.path.insert(0, str(P.PARENT / "tradfi"))
from _common import Http, merge_rows, read_rows, write_rows  # type: ignore


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-lock", action="store_true", help="调用方已持 flock 时使用")
    ap.add_argument("--force", action="store_true", help="跳过 preflight（调试）")
    ap.add_argument("--after-tradfi", action="store_true",
                    help="链式挂在 tradfi 日更后：忽略 tradfi 日更锁")
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

        uni = P.load_universe(refresh=True)
        assets = uni["assets"]
        print(f"dynamic top-{len(assets)} (rank_by={uni.get('rank_by')})", flush=True)
        http = Http(min_interval_s=0.25, timeout=30, retries=3)
        ok_n = err_n = 0
        t0 = time.time()
        for a in assets:
            base = a["base_asset"]
            venue = a["venue"]
            yahoo = a["yahoo"]
            sector = a["sector"]
            try:
                fresh = fetch_daily_bars(http, yahoo, max_rows=P.MAX_DAILY_ROWS)
            except Exception as e:  # noqa: BLE001
                print(f"  ERR {base}: {e}", flush=True)
                err_n += 1
                continue
            if not fresh:
                print(f"  EMPTY {base} ({yahoo})", flush=True)
                err_n += 1
                continue
            path = P.spot_price_file(venue, base, "1d")
            merged = merge_rows(read_rows(path), fresh)
            if len(merged) > P.MAX_DAILY_ROWS:
                merged = merged[-P.MAX_DAILY_ROWS:]
            write_rows(path, merged, meta={
                "venue": venue,
                "symbol": yahoo,
                "base_asset": base,
                "sector": sector,
                "interval": "1d",
                "form": "spot",
                "source": "yahoo",
            })
            ok_n += 1
            print(f"  OK {base:<8} rows={len(merged)} yahoo={yahoo} vol1d={a.get('volume_usd_1d')}", flush=True)

        print(f"done ok={ok_n} err={err_n} elapsed={time.time()-t0:.1f}s", flush=True)
        return 0 if ok_n else 1
    finally:
        if not args.no_lock:
            lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
