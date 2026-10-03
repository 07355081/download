"""Shared paths for cex-asset&vol.

Layout:
  cache/        pipeline private CSVs + xlsx (not read by dashboard)
  output/json/  publish surface → copy_to_dashboard --only cex
"""
from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"
OUTPUT_JSON_DIR = BASE_DIR / "output" / "json"

MAJOR_VOL_CSV = CACHE_DIR / "major_vol_切勿删除.csv"
BTC_HOURLY_CSV = CACHE_DIR / "btc_hourly_data.csv"
BTC_DAILY_VWAP_CSV = CACHE_DIR / "btc_daily_vwap.csv"
ASSET_WIDE_CSV = CACHE_DIR / "cex_total_assets_daily_wide.csv"
BTC_BALANCE_CSV = CACHE_DIR / "cex_exchange_daily_balance_btc.csv"
ETH_BALANCE_CSV = CACHE_DIR / "cex_exchange_daily_balance_eth.csv"
STABLECOIN_BALANCE_CSV = CACHE_DIR / "cex_exchange_daily_balance_stablecoins.csv"
FACTOR_DAILY_CSV = CACHE_DIR / "cex_dynamic_factors_daily.csv"
PIPELINE_XLSX = CACHE_DIR / "cex_volume_pipeline_unified.xlsx"

# Filenames that used to live in the module root; migrated into cache/ once.
_LEGACY_CACHE_FILES = (
    "major_vol_切勿删除.csv",
    "btc_hourly_data.csv",
    "btc_daily_vwap.csv",
    "cex_total_assets_daily_wide.csv",
    "cex_exchange_daily_balance_btc.csv",
    "cex_exchange_daily_balance_eth.csv",
    "cex_exchange_daily_balance_stablecoins.csv",
    "cex_dynamic_factors_daily.csv",
    "cex_volume_pipeline_unified.xlsx",
)


def ensure_cache_layout() -> list[str]:
    """Move legacy root-level artifacts into cache/. Returns log lines."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON_DIR.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    for name in _LEGACY_CACHE_FILES:
        src = BASE_DIR / name
        dst = CACHE_DIR / name
        if not src.is_file():
            continue
        if dst.is_file():
            # Prefer the newer copy in cache/.
            if src.stat().st_mtime > dst.stat().st_mtime:
                dst.write_bytes(src.read_bytes())
                notes.append(f"updated cache/{name} from root (newer)")
            src.unlink()
            notes.append(f"removed legacy root/{name}")
        else:
            src.rename(dst)
            notes.append(f"moved {name} → cache/")
    return notes
