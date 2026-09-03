"""Coinglass 币种与交易对元数据：一次刷新 coins + exchange-pairs 四类接口。

API（均需 CG-API-KEY）：
  - GET /api/futures/supported-coins
  - GET /api/futures/supported-exchange-pairs
  - GET /api/spot/supported-coins
  - GET /api/spot/supported-exchange-pairs

额外（非上述 4 接口）：
  - top200：本地 tag 留底 + coins-markets 兜底 → symbols/{market}_top200.json

输出目录 symbols/：
  - {market}_all.json           ← supported-coins（币种，按 symbol 下载用）
  - {market}_instruments.json   ← supported-exchange-pairs（交易所×交易对）
  - {market}_top200.json          ← 热门 200 币（非 API 四接口之一）

用法：
  python _coin_pairs.py              # 刷新 futures + spot 的 coins 与 pairs
  python _coin_pairs.py futures
  python _coin_pairs.py spot
  python _coin_pairs.py top200       # 仅刷新 top200
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from _common import API_BASE, ROOT_DIR, TRACKED_EXCHANGES, http_get_json, resolve_api_key

SYMBOLS_DIR = ROOT_DIR / "symbols"
INSTRUMENTS_DIR = SYMBOLS_DIR  # 兼容旧名

TRACKED = frozenset(TRACKED_EXCHANGES)
SPOT_QUOTES = frozenset({"USDT", "USDC", "USD"})
TAG_TOP200_JSON = ROOT_DIR.parent / "tag" / "output" / "json" / "coinglass_top200_留底.json"

_PAIR_KEYS = {
    "instrument_id": ("instrument_id", "instrumentId", "symbol", "pair"),
    "base_asset": ("base_asset", "baseAsset", "base"),
    "quote_asset": ("quote_asset", "quoteAsset", "quote"),
}


def all_supported_path(market: str) -> Path:
    return SYMBOLS_DIR / f"{market}_all.json"


def instruments_path(market: str) -> Path:
    return SYMBOLS_DIR / f"{market}_instruments.json"


def top200_path(market: str) -> Path:
    return SYMBOLS_DIR / f"{market}_top200.json"


def _pick(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        val = row.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def _write_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _api_get(path: str, api_key: str) -> Any:
    status, payload = http_get_json(f"{API_BASE}{path}", api_key=api_key)
    if status != 200 or not isinstance(payload, dict) or str(payload.get("code")) != "0":
        raise SystemExit(f"API 失败 {path}: HTTP {status} body={payload!r}")
    return payload.get("data")


def _dedupe_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        s = str(item or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


# ── supported-coins ────────────────────────────────────────────────


def refresh_coins(market: str, api_key: str | None = None) -> list[str]:
    """拉取 supported-coins，写入 symbols/{market}_all.json。"""
    key = api_key or resolve_api_key()
    raw = _api_get(f"/api/{market}/supported-coins", key)
    if not isinstance(raw, list):
        raise SystemExit(f"supported-coins 返回类型异常: {market}")
    symbols = _dedupe_strings([str(x) for x in raw])
    if not symbols:
        raise SystemExit(f"supported-coins 为空: {market}")
    _write_json(
        all_supported_path(market),
        {
            "market": market,
            "scope": "all",
            "source": f"/api/{market}/supported-coins",
            "symbols": symbols,
            "count": len(symbols),
            "tracked_exchanges": list(TRACKED_EXCHANGES),
            "updated_at": int(time.time()),
        },
    )
    return symbols


def refresh_all_supported(market: str, api_key: str | None = None) -> list[str]:
    """refresh_coins 的别名，供 _common.load_symbols(scope=all) 调用。"""
    return refresh_coins(market, api_key)


# ── supported-exchange-pairs ─────────────────────────────────────


def _parse_pair(market: str, row: Any) -> dict[str, str] | None:
    if not isinstance(row, dict):
        return None
    instrument_id = _pick(row, *_PAIR_KEYS["instrument_id"])
    if not instrument_id:
        return None
    base_asset = _pick(row, *_PAIR_KEYS["base_asset"])
    quote_asset = _pick(row, *_PAIR_KEYS["quote_asset"])
    if market == "spot" and quote_asset and quote_asset.upper() not in SPOT_QUOTES:
        return None
    return {
        "instrument_id": instrument_id,
        "base_asset": base_asset,
        "quote_asset": quote_asset,
    }


def refresh_pairs(market: str, api_key: str | None = None) -> dict[str, list[dict[str, str]]]:
    """拉取 supported-exchange-pairs，写入 symbols/{market}_instruments.json。"""
    key = api_key or resolve_api_key()
    raw = _api_get(f"/api/{market}/supported-exchange-pairs", key)
    if not isinstance(raw, dict):
        raise SystemExit(f"supported-exchange-pairs 返回类型异常: {market}")

    by_exchange: dict[str, list[dict[str, str]]] = {}
    for ex_name, items in raw.items():
        exchange = str(ex_name or "").strip()
        if exchange not in TRACKED or not isinstance(items, list):
            continue
        pairs = [p for row in items if (p := _parse_pair(market, row))]
        if pairs:
            by_exchange[exchange] = pairs

    _write_json(
        instruments_path(market),
        {
            "market": market,
            "source": f"/api/{market}/supported-exchange-pairs",
            "by_exchange": by_exchange,
            "pair_count": sum(len(v) for v in by_exchange.values()),
            "updated_at": int(time.time()),
        },
    )
    return by_exchange


def refresh_instruments(market: str, api_key: str | None = None) -> dict[str, list[dict[str, str]]]:
    """refresh_pairs 的别名，供 _common.load_instruments 调用。"""
    return refresh_pairs(market, api_key)


# ── top200（本地 tag，非四接口之一）────────────────────────────────


def _symbols_from_tag(market: str) -> list[str]:
    if not TAG_TOP200_JSON.is_file():
        return []
    try:
        payload = json.loads(TAG_TOP200_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    data = payload.get("data")
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("币种") or row.get("symbol") or "").strip()
        if not sym:
            continue
        mode = str(row.get("现货/合约/都有") or "")
        if market == "futures" and "合约" not in mode and mode != "都有":
            continue
        if market == "spot" and "现货" not in mode and mode != "都有":
            continue
        out.append(sym)
    return _dedupe_strings(out)[:200]


def _symbol_from_market_row(row: dict[str, Any]) -> str | None:
    for k in ("symbol", "coin", "base_coin", "currency", "name"):
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().upper()
    return None


def _symbols_from_coins_markets(market: str, api_key: str) -> list[str]:
    status, payload = http_get_json(
        f"{API_BASE}/api/{market}/coins-markets",
        api_key=api_key,
    )
    if status != 200 or not isinstance(payload, dict) or str(payload.get("code")) != "0":
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    scored: list[tuple[float, str]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        sym = _symbol_from_market_row(row)
        if not sym:
            continue
        vol = 0.0
        for vk in (
            "volume_usd_24h",
            "vol_usd_24h",
            "volume_usd",
            "turnover_24h",
            "open_interest_usd",
            "open_interest",
        ):
            try:
                vol = float(row.get(vk) or 0)
                if vol > 0:
                    break
            except (TypeError, ValueError):
                continue
        scored.append((vol, sym))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out: list[str] = []
    for _, sym in scored:
        if sym in out:
            continue
        out.append(sym)
        if len(out) >= 200:
            break
    return out


def refresh_top200(market: str, api_key: str | None = None) -> list[str]:
    """刷新 top200 列表（tag 优先，不足时用 coins-markets）。"""
    if market not in ("futures", "spot"):
        raise ValueError(f"unsupported market: {market!r}")
    key = api_key or resolve_api_key()
    symbols = _symbols_from_tag(market)
    if len(symbols) < 50:
        symbols = _symbols_from_coins_markets(market, key)
    if not symbols:
        raise SystemExit(f"Could not resolve top-200 symbols for {market}")
    _write_json(
        top200_path(market),
        {
            "market": market,
            "symbols": symbols,
            "count": len(symbols),
            "tracked_exchanges": list(TRACKED_EXCHANGES),
            "updated_at": int(time.time()),
        },
    )
    return symbols


# ── 批量刷新 ─────────────────────────────────────────────────────


def refresh_market(market: str, api_key: str | None = None) -> None:
    """刷新某一市场的 supported-coins + supported-exchange-pairs。"""
    key = api_key or resolve_api_key()
    coins = refresh_coins(market, key)
    pairs = refresh_pairs(market, key)
    total_pairs = sum(len(v) for v in pairs.values())
    print(f"[{market}] coins={len(coins)}  pairs={total_pairs}  exchanges={len(pairs)}")


def refresh_all(api_key: str | None = None) -> None:
    """刷新全部 4 个 Coinglass 列表接口（futures + spot）。"""
    key = api_key or resolve_api_key()
    for market in ("futures", "spot"):
        refresh_market(market, key)


if __name__ == "__main__":
    arg = (sys.argv[1] if len(sys.argv) > 1 else "all").strip().lower()
    if arg == "all":
        refresh_all()
    elif arg == "top200":
        key = resolve_api_key()
        for m in ("futures", "spot"):
            syms = refresh_top200(m, key)
            print(f"[{m}] top200={len(syms)}")
    elif arg in ("futures", "spot"):
        refresh_market(arg)
    else:
        sys.exit("用法: python _coin_pairs.py [all|futures|spot|top200]")
