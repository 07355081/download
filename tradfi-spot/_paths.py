"""tradfi-spot 路径与共享常量。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PARENT = ROOT.parent
OUTPUT = ROOT / "output" / "json"
SPOT_PRICE_DIR = OUTPUT / "tradfi-spot-price"
ARB_SPREAD_DIR = OUTPUT / "tradfi-arb-spread"
LIVE_PATH = OUTPUT / "tradfi-arb-live.json"
UNIVERSE_PATH = ROOT / "universe.json"
UNIVERSE_OUT = OUTPUT / "tradfi-spot-universe.json"
LOCK_DIR = ROOT / ".locks"
SPOT_LOCK = LOCK_DIR / "tradfi-spot.lock"
# 与现有日更互斥：对方持锁则本模块 skip
TRADFI_DAILY_LOCK = PARENT / "tradfi" / ".daily.lock"
COINGLASS_DAILY_LOCK = PARENT / "coinglass-history" / ".daily.lock"
MISC_DAILY_LOCK = PARENT / ".misc_daily.lock"

TRADFI_SYMBOLS = PARENT / "tradfi" / "output" / "json" / "tradfi-symbols.json"
TRADFI_PRICE_DIR = PARENT / "tradfi" / "output" / "json" / "tradfi-price"

SCHEDULE_SEC = 3600
REF_STALE_MS = 2 * SCHEDULE_SEC * 1000
DEFAULT_SECTOR_LIMITS = {"Stocks": 70, "Commodities": 15, "Indices": 15}

# 复用 tradfi HTTP / 归一化
sys.path.insert(0, str(PARENT / "tradfi"))
sys.path.insert(0, str(PARENT))


def load_universe(*, refresh: bool = False) -> dict:
    """加载动态 Top-N universe（按 tradfi 最近 1d 成交量）。"""
    from _rank import load_universe as _load_dynamic  # noqa: WPS433

    return _load_dynamic(refresh=refresh)


def load_universe_cached() -> dict:
    """优先读已生成的 universe 快照；不存在则现场计算。"""
    if UNIVERSE_OUT.is_file():
        try:
            doc = json.loads(UNIVERSE_OUT.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and doc.get("assets"):
                return doc
        except (OSError, json.JSONDecodeError):
            pass
    return load_universe(refresh=True)


def ensure_dirs() -> None:
    for d in (LOCK_DIR, OUTPUT):
        d.mkdir(parents=True, exist_ok=True)


def safe_seg(value: str) -> str:
    return "".join(c if (c.isalnum() or c in "._-") else "-" for c in value)


def spot_price_file(venue: str, symbol: str, interval: str = "1d") -> Path:
    return SPOT_PRICE_DIR / f"{safe_seg(venue)}_{safe_seg(symbol)}_{interval}.json"


def arb_spread_file(base: str, interval: str = "1d") -> Path:
    return ARB_SPREAD_DIR / f"{safe_seg(base)}_{interval}.json"


def atomic_write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
