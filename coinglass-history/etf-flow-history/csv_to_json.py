"""cache/<market>.csv -> output/json/<market>.json"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import _common as C  # noqa: E402
import _json_merge as JM  # noqa: E402

HERE = C.module_dir(__file__)
CACHE_DIR = HERE / "cache"
JSON_OUT_DIR = HERE / "output" / "json"

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--market", default=None)
    args = p.parse_args()
    if not CACHE_DIR.exists(): raise SystemExit(f"cache dir not found: {CACHE_DIR}")
    JSON_OUT_DIR.mkdir(parents=True, exist_ok=True)
    m_f = set(m.strip() for m in args.market.split(",")) if args.market else None
    written = 0
    for cf in sorted(CACHE_DIR.glob("*.csv")):
        market = cf.stem
        if m_f and market not in m_f: continue
        rows = C.read_ohlc_cache(cf)
        if not rows: continue
        candles = []
        for ts in sorted(rows.keys()):
            r = rows[ts]; c = {"time": ts}
            for k in ("open", "high", "low", "close", "volume_usd"):
                v = r.get(k)
                if v not in (None, ""):
                    try: c[k] = float(v)
                    except (ValueError, TypeError): pass
            extra = r.get("extra_json") or ""
            if extra:
                try:
                    eo = json.loads(extra)
                    if isinstance(eo, dict):
                        for k, v in eo.items(): c.setdefault(k, v)
                except json.JSONDecodeError: pass
            candles.append(c)
        payload = {"code": "0", "msg": "success", "market": market, "data": candles}
        out = JSON_OUT_DIR / f"{market}.json"
        JM.write_coinglass_candles_json(out, payload)
        print(f"  wrote {out.name}  ({len(candles)} rows, {out.stat().st_size:,} bytes)")
        written += 1
    print(f"\nDONE. wrote={written} -> {JSON_OUT_DIR}")

