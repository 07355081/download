# -*- coding: utf-8 -*-
"""Backfill null exchange columns in major_vol for a month window (slow, days=30)."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

BASE = Path(__file__).resolve().parent
for p in (BASE.parent / ".env", Path("/root/dashboard/.env.live")):
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from _paths import MAJOR_VOL_CSV  # noqa: E402

KEY = (
    os.environ.get("COINGECKO_DEMO_API_KEY") or os.environ.get("COINGECKO_KEY") or ""
).strip()
HEADERS = {"accept": "application/json", "User-Agent": "wublock-backfill"}
if KEY:
    HEADERS["x-cg-demo-api-key"] = KEY

# columns that commonly go missing
COLS = [
    "okex",
    "okex_swap",
    "bybit_spot",
    "bybit",
    "bitget",
    "bitget_futures",
    "huobi",
    "huobi_dm",
    "mxc",
    "mxc_futures",
    "kucoin",
    "kumex",
    "upbit",
    "crypto_com",
    "crypto_com_futures",
    "hyperliquid-spot",
    "hyperliquid",
    "gate",
    "gate_futures",
    "deribit",
    "kraken_futures",
    "coinbase_international",
    "gdax",
]
# legacy CSV column ← current CoinGecko id
COL_ALIASES = {
    "coinbase_international": "coinbase_international_derivatives",
}
START = "2026-10-01"
END = "2026-10-31"
SLEEP = 4.0


def fetch(eid: str) -> dict[str, float]:
    url = f"https://api.coingecko.com/api/v3/exchanges/{eid}/volume_chart"
    for attempt in range(5):
        r = requests.get(url, headers=HEADERS, params={"days": "90"}, timeout=60)
        if r.status_code == 429:
            wait = 20 * (attempt + 1)
            print(f"  429 {eid}, sleep {wait}s")
            time.sleep(wait)
            continue
        if r.status_code == 404:
            print(f"  404 {eid}")
            return {}
        r.raise_for_status()
        raw = r.json()
        out: dict[str, float] = {}
        if isinstance(raw, list):
            for pt in raw:
                if isinstance(pt, list) and len(pt) >= 2:
                    day = datetime.fromtimestamp(int(pt[0]) / 1000, tz=timezone.utc).strftime(
                        "%Y-%m-%d"
                    )
                    if pt[1] is None:
                        continue
                    out[day] = out.get(day, 0.0) + float(pt[1])
        return out
    return {}


def main() -> int:
    df = pd.read_csv(MAJOR_VOL_CSV, encoding="utf-8-sig")
    date_col = df.columns[0]
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce").dt.strftime("%Y-%m-%d")
    mask = (df[date_col] >= START) & (df[date_col] <= END)
    filled = 0
    for eid in COLS:
        target = COL_ALIASES.get(eid, eid)
        if target not in df.columns:
            # allow creating alias target from legacy empty col, else skip
            if eid in df.columns:
                target = eid
            else:
                print("skip missing col", eid, "->", target)
                continue
        need = int(df.loc[mask, target].isna().sum())
        if need == 0:
            print(eid, "->", target, "already full")
            continue
        print(f"fetch {eid} -> {target} (need {need})")
        daily = fetch(eid)
        time.sleep(SLEEP)
        if not daily:
            print("  empty")
            continue
        for i in df.index[mask]:
            day = df.at[i, date_col]
            if pd.isna(df.at[i, target]) and day in daily:
                df.at[i, target] = daily[day]
                filled += 1
        print(f"  filled cells now nn={df.loc[mask, target].notna().sum()}")
    # recompute ALL
    data_cols = [c for c in df.columns if c not in (date_col, "ALL")]
    df["ALL"] = df[data_cols].apply(pd.to_numeric, errors="coerce").sum(axis=1, min_count=1)
    tmp = MAJOR_VOL_CSV.with_suffix(".csv.tmp")
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    tmp.replace(MAJOR_VOL_CSV)
    print("wrote", MAJOR_VOL_CSV, "filled", filled)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
