"""cache/<market>_<ticker>.csv -> output/json/<market>_<ticker>.json"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import _common as C  # noqa: E402
import _json_merge as JM  # noqa: E402
from _runner import _rows_to_candles  # noqa: E402

HERE = C.module_dir(__file__)
CACHE_DIR = HERE / "cache"
JSON_OUT_DIR = HERE / "output" / "json"

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--market", default=None)
    p.add_argument("--ticker", default=None)
    args = p.parse_args()
    m_f = set(m.strip() for m in args.market.split(",")) if args.market else None
    t_f = set(t.strip() for t in args.ticker.split(",")) if args.ticker else None
    JSON_OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for cf in sorted(CACHE_DIR.glob("*.csv")):
        stem = cf.stem
        if "_" not in stem:
            continue
        market, ticker = stem.split("_", 1)
        if m_f and market not in m_f:
            continue
        if t_f and ticker not in t_f:
            continue
        rows = C.read_ohlc_cache(cf)
        if not rows:
            continue
        payload = {
            "code": "0",
            "msg": "success",
            "market": market,
            "ticker": ticker,
            "data": _rows_to_candles(rows),
        }
        out = JSON_OUT_DIR / f"{stem}.json"
        JM.write_coinglass_candles_json(out, payload)
        written += 1
    print(f"DONE. wrote={written} -> {JSON_OUT_DIR}")
    # CoinGlass 独立溢价端点已冻在 2026-05-15，首页/报告/ETF 页都读那份。
    # 单 ticker 重跑时不重建，避免用残缺集合把完整快照盖掉。
    if written and not t_f:
        sys.path.insert(0, str(HERE))
        from aggregate_premium import main as rebuild_premium

        rebuild_premium()
