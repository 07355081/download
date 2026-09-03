"""Wide cache -> JSON list[{timestamp, list:[{ticker,...}]}]."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import _common as C  # noqa: E402
import _json_merge as JM  # noqa: E402

HERE = C.module_dir(__file__)
CACHE_DIR = HERE / "cache"
JSON_OUT_DIR = HERE / "output" / "json"
PRESERVED = ("nav_usd", "market_price_usd", "premium_discount_details")


def main() -> None:
    JSON_OUT_DIR.mkdir(parents=True, exist_ok=True)
    for cf in CACHE_DIR.glob("*.csv"):
        market = cf.stem
        wide = C.read_wide_cache(cf)
        if not wide:
            continue
        by_ts: dict[int, dict[str, dict[str, float]]] = defaultdict(dict)
        for (ts, key), val in wide.items():
            if "__" not in key:
                continue
            ticker, field = key.split("__", 1)
            if field not in PRESERVED:
                continue
            by_ts[ts].setdefault(ticker, {})[field] = val
        data = []
        for ts in sorted(by_ts.keys()):
            sub = [{"ticker": tk, **fields} for tk, fields in sorted(by_ts[ts].items())]
            data.append({"timestamp": ts, "list": sub})
        payload = {"code": "0", "msg": "success", "market": market, "data": data}
        JM.write_dashboard_json(
            JSON_OUT_DIR / f"{market}.json",
            payload,
            merge_data_fn=JM.merge_etf_premium_data,
        )
        print(f"  wrote {market}.json  ({len(data)} timestamps)")


if __name__ == "__main__":
    main()
