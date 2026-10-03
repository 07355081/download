"""按 tradfi-price 最近 1d 成交量动态选 Top N（分板块），无额外网络请求。"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import _paths as P

from _common import CRYPTO_OVERRIDE, normalize_base, strip_stock_suffix, to_num  # type: ignore

_STOCK_TICKER = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")


def load_config() -> dict[str, Any]:
    raw = json.loads(P.UNIVERSE_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"invalid config: {P.UNIVERSE_PATH}")
    return raw


def _last_volume_usd(path: Path) -> float | None:
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    rows = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    return to_num(rows[-1].get("volume_usd"))


def aggregate_volume_by_base() -> dict[str, dict[str, Any]]:
    """返回 {base_asset: {sector, volume_usd}}，volume 为各所最新 1d 之和。"""
    if not P.TRADFI_SYMBOLS.is_file():
        return {}
    try:
        doc = json.loads(P.TRADFI_SYMBOLS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    symbols = doc.get("symbols") or []
    vol: dict[str, float] = defaultdict(float)
    meta: dict[str, dict[str, Any]] = {}
    for s in symbols:
        if not isinstance(s, dict) or (s.get("form") or "perp") != "perp":
            continue
        sector = s.get("sector")
        if sector not in ("Stocks", "Commodities", "Indices"):
            continue
        base = normalize_base(strip_stock_suffix(str(s.get("base_asset") or "")))
        if not base:
            continue
        ex = str(s.get("exchange") or "")
        iid = str(s.get("instrument_id") or "")
        path = P.TRADFI_PRICE_DIR / f"{P.safe_seg(ex)}_{P.safe_seg(iid)}_1d.json"
        v = _last_volume_usd(path)
        if v is not None and v > 0:
            vol[base] += v
        if base not in meta:
            meta[base] = {"sector": sector}
    out: dict[str, dict[str, Any]] = {}
    for base, total in vol.items():
        if total <= 0:
            continue
        out[base] = {"sector": meta[base]["sector"], "volume_usd": total}
    return out


def resolve_yahoo(base: str, sector: str, cfg: dict[str, Any]) -> str | None:
    block = set(cfg.get("blocklist") or []) | CRYPTO_OVERRIDE
    if base in block:
        return None
    ymap = cfg.get("yahoo_map") or {}
    if base in ymap:
        return ymap[base]
    b = strip_stock_suffix(base)
    if sector == "Stocks":
        if not _STOCK_TICKER.match(b):
            return None
        # 自动映射仅限常见美股 ticker 长度（避免 SKHYNIX / SAMSUNG 等无 Yahoo 符号）
        if 1 <= len(b) <= 5:
            return b
        return None
    if sector == "Indices":
        if b in ymap:
            return ymap[b]
        # 指数 ETF 如 SPY/QQQ/IWM 直接用 ticker
        if 1 <= len(b) <= 5 and _STOCK_TICKER.match(b):
            return b
        return None
    if sector == "Commodities":
        return ymap.get(base) or ymap.get(b)
    return None


def resolve_venue(base: str, sector: str, cfg: dict[str, Any]) -> str:
    vmap = cfg.get("venue_map") or {}
    if base in vmap:
        return vmap[base]
    defaults = cfg.get("venue_default") or {}
    return str(defaults.get(sector) or "NASDAQ")


def build_dynamic_universe(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """生成本轮 Top N universe（含排名与 1d 成交量）。"""
    cfg = cfg or load_config()
    limits: dict[str, int] = cfg.get("sector_limits") or P.DEFAULT_SECTOR_LIMITS
    vol_map = aggregate_volume_by_base()
    by_sector: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for base, info in vol_map.items():
        sector = info["sector"]
        yahoo = resolve_yahoo(base, sector, cfg)
        if not yahoo:
            continue
        by_sector[sector].append((base, float(info["volume_usd"])))

    assets: list[dict[str, Any]] = []
    for sector in ("Stocks", "Commodities", "Indices"):
        limit = int(limits.get(sector) or 0)
        if limit <= 0:
            continue
        ranked = sorted(by_sector.get(sector) or [], key=lambda x: x[1], reverse=True)
        for rank, (base, vol_usd) in enumerate(ranked[:limit], start=1):
            assets.append({
                "base_asset": base,
                "sector": sector,
                "venue": resolve_venue(base, sector, cfg),
                "yahoo": resolve_yahoo(base, sector, cfg),
                "volume_usd_1d": round(vol_usd, 2),
                "rank": rank,
            })

    return {
        "generated_at": int(time.time()),
        "source": cfg.get("source", "yahoo"),
        "schedule_sec": cfg.get("schedule_sec", P.SCHEDULE_SEC),
        "rank_by": cfg.get("rank_by", "tradfi-price_1d_volume_usd"),
        "count": len(assets),
        "assets": assets,
    }


def load_universe(*, refresh: bool = True) -> dict[str, Any]:
    """动态 universe；refresh=True 时重算并写入 tradfi-spot-universe.json。"""
    uni = build_dynamic_universe()
    if refresh:
        P.ensure_dirs()
        P.atomic_write_json(P.UNIVERSE_OUT, uni)
    return uni
