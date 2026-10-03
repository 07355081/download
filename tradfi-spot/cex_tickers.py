"""加密所 TradFi perp last：优先每所一次 bulk ticker；失败则回退本地 tradfi-price 最新 close。"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import _paths as P
from _common import Http, normalize_base, to_num  # type: ignore


def load_focus_instruments(focus_bases: set[str]) -> list[dict]:
    """从 tradfi-symbols.json 筛 form=perp 且 base 在 focus 内的腿。"""
    if not P.TRADFI_SYMBOLS.exists():
        return []
    try:
        doc = json.loads(P.TRADFI_SYMBOLS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for s in doc.get("symbols") or []:
        if not isinstance(s, dict):
            continue
        if (s.get("form") or "perp") != "perp":
            continue
        base = normalize_base(str(s.get("base_asset") or ""))
        if base not in focus_bases:
            continue
        out.append({
            "exchange": s.get("exchange"),
            "instrument_id": s.get("instrument_id"),
            "base_asset": base,
            "sector": s.get("sector"),
        })
    return out


def fetch_lasts(http: Http, instruments: list[dict]) -> dict[tuple[str, str], dict]:
    """返回 {(exchange, instrument_id): {last, asof_ms, source}}。"""
    by_ex: dict[str, list[dict]] = {}
    for inst in instruments:
        by_ex.setdefault(str(inst["exchange"]), []).append(inst)

    result: dict[tuple[str, str], dict] = {}
    fetchers = {
        "OKX": _okx,
        "Gate": _gate,
        "Bitget": _bitget,
        "Bybit": _bybit,
        "Binance": _binance,
        "MEXC": _mexc,
        "HTX": _htx,
        "Crypto.com": _cryptocom,
        "Coinbase": _coinbase,
        "Kraken": _kraken,
        "Hyperliquid": _hyperliquid,
    }
    now = int(time.time() * 1000)
    for ex, insts in by_ex.items():
        fn = fetchers.get(ex)
        got: dict[str, float] = {}
        if fn is not None:
            try:
                got = fn(http, {i["instrument_id"] for i in insts})
            except Exception:  # noqa: BLE001
                got = {}
        for inst in insts:
            iid = inst["instrument_id"]
            key = (ex, iid)
            if iid in got and got[iid] is not None:
                result[key] = {"last": got[iid], "asof_ms": now, "source": "ticker"}
            else:
                fb = _fallback_local(ex, iid)
                if fb is not None:
                    result[key] = fb
    return result


def _fallback_local(exchange: str, instrument_id: str) -> dict | None:
    """读本地 tradfi-price 1h 或 1d 最新 close（不整目录扫描）。"""
    for iv in ("1h", "1d"):
        path = P.TRADFI_PRICE_DIR / f"{_safe(exchange)}_{_safe(instrument_id)}_{iv}.json"
        row = _latest_close(path)
        if row:
            return row
    return None


def _safe(v: str) -> str:
    return "".join(c if (c.isalnum() or c in "._-") else "-" for c in v)


def _latest_close(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    data = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(data, list) or not data:
        return None
    last = data[-1]
    close = to_num(last.get("close"))
    tm = last.get("time")
    if close is None:
        return None
    return {"last": close, "asof_ms": int(tm) if isinstance(tm, (int, float)) else int(time.time() * 1000),
            "source": "local_json"}


# ───── per-exchange bulk ─────


def _okx(http: Http, want: set[str]) -> dict[str, float]:
    res = http.get_json("https://www.okx.com/api/v5/market/tickers?instType=SWAP")
    if not res.ok:
        return {}
    out = {}
    for r in (res.data or {}).get("data") or []:
        iid = r.get("instId") or ""
        if iid in want:
            px = to_num(r.get("last"))
            if px is not None:
                out[iid] = px
    return out


def _gate(http: Http, want: set[str]) -> dict[str, float]:
    res = http.get_json("https://api.gateio.ws/api/v4/futures/usdt/tickers")
    if not res.ok or not isinstance(res.data, list):
        return {}
    out = {}
    for r in res.data:
        iid = r.get("contract") or ""
        if iid in want:
            px = to_num(r.get("last"))
            if px is not None:
                out[iid] = px
    return out


def _bitget(http: Http, want: set[str]) -> dict[str, float]:
    res = http.get_json("https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES")
    if not res.ok:
        return {}
    out = {}
    for r in (res.data or {}).get("data") or []:
        iid = r.get("symbol") or ""
        if iid in want:
            px = to_num(r.get("lastPr") or r.get("last"))
            if px is not None:
                out[iid] = px
    return out


def _bybit(http: Http, want: set[str]) -> dict[str, float]:
    res = http.get_json("https://api.bybit.com/v5/market/tickers?category=linear")
    if not res.ok:
        return {}
    out = {}
    for r in ((res.data or {}).get("result") or {}).get("list") or []:
        iid = r.get("symbol") or ""
        if iid in want:
            px = to_num(r.get("lastPrice"))
            if px is not None:
                out[iid] = px
    return out


def _binance(http: Http, want: set[str]) -> dict[str, float]:
    # 主站可能 451；备用 data-api
    res = http.get_json_multi([
        "https://fapi.binance.com/fapi/v1/ticker/price",
        "https://fstream.binance.com/fapi/v1/ticker/price",
    ])
    if not res.ok or not isinstance(res.data, list):
        return {}
    out = {}
    for r in res.data:
        iid = r.get("symbol") or ""
        if iid in want:
            px = to_num(r.get("price"))
            if px is not None:
                out[iid] = px
    return out


def _mexc(http: Http, want: set[str]) -> dict[str, float]:
    res = http.get_json_multi([
        "https://futures.mexc.com/api/v1/contract/ticker",
        "https://contract.mexc.com/api/v1/contract/ticker",
    ])
    if not res.ok:
        return {}
    data = res.data.get("data") if isinstance(res.data, dict) else res.data
    if not isinstance(data, list):
        # 单票结构
        if isinstance(data, dict):
            data = [data]
        else:
            return {}
    out = {}
    for r in data:
        iid = r.get("symbol") or ""
        if iid in want:
            px = to_num(r.get("lastPrice") or r.get("fairPrice"))
            if px is not None:
                out[iid] = px
    return out


def _htx(http: Http, want: set[str]) -> dict[str, float]:
    res = http.get_json_multi([
        "https://api.hbdm.vn/linear-swap-ex/market/detail/batch_merged",
        "https://api.hbdm.com/linear-swap-ex/market/detail/batch_merged",
    ])
    if not res.ok:
        return {}
    ticks = (res.data or {}).get("ticks") or (res.data or {}).get("data") or []
    out = {}
    for r in ticks:
        iid = r.get("contract_code") or r.get("symbol") or ""
        if iid in want:
            px = to_num(r.get("close") or r.get("last"))
            if px is not None:
                out[iid] = px
    return out


def _cryptocom(http: Http, want: set[str]) -> dict[str, float]:
    res = http.get_json("https://api.crypto.com/exchange/v1/public/get-tickers")
    if not res.ok:
        return {}
    data = ((res.data or {}).get("result") or {}).get("data") or []
    out = {}
    for r in data:
        iid = r.get("i") or r.get("instrument_name") or ""
        if iid in want:
            px = to_num(r.get("a") or r.get("k") or r.get("last"))  # a=ask 近似
            if px is None:
                px = to_num(r.get("k"))
            if px is not None:
                out[iid] = px
    return out


def _coinbase(http: Http, want: set[str]) -> dict[str, float]:
    # INTX instruments 无统一 bulk last；逐票过贵，直接依赖 local fallback
    return {}


def _kraken(http: Http, want: set[str]) -> dict[str, float]:
    res = http.get_json("https://futures.kraken.com/derivatives/api/v3/tickers")
    if not res.ok:
        return {}
    out = {}
    for r in (res.data or {}).get("tickers") or []:
        iid = str(r.get("symbol") or "")
        if iid in want:
            px = to_num(r.get("last") or r.get("markPrice"))
            if px is not None:
                out[iid] = px
    return out


def _hyperliquid(http: Http, want: set[str]) -> dict[str, float]:
    """HIP-3 bulk mid：按 dex 一次 allMids，禁止逐 symbol K 线。"""
    by_dex: dict[str, set[str]] = {}
    for iid in want:
        if ":" in iid:
            by_dex.setdefault(iid.split(":", 1)[0], set()).add(iid)
    out: dict[str, float] = {}
    for dex, ids in by_dex.items():
        res = http.post_json("https://api.hyperliquid.xyz/info", {"type": "allMids", "dex": dex})
        if not res.ok or not isinstance(res.data, dict):
            continue
        for iid in ids:
            px = to_num(res.data.get(iid))
            if px is not None:
                out[iid] = px
    return out
