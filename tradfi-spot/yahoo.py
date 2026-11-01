"""Yahoo Finance：quote（快照）+ chart 1d（历史）。无额外依赖，走 tradfi Http。"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

from _common import Http, to_ms, to_num  # type: ignore  # noqa: E402

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
QUOTE = "https://query1.finance.yahoo.com/v7/finance/quote"
# v7 偶发 401，备选 crumb-less chart meta
QUOTE_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"


def fetch_daily_bars(http: Http, yahoo_symbol: str, *, max_rows: int = 5000) -> list[dict]:
    """拉尽可能长的日线 OHLC，返回 [{time, open, high, low, close, volume_usd}]。"""
    sym = quote(yahoo_symbol, safe="^=")
    # range=max 对部分期货可用；失败再退 period1/period2
    urls = [
        f"{CHART.format(symbol=sym)}?interval=1d&range=max",
        f"{CHART.format(symbol=sym)}?interval=1d&range=10y",
        f"{CHART.format(symbol=sym)}?interval=1d&period1=0&period2={int(time.time())}",
    ]
    res = http.get_json_multi(urls)
    if not res.ok:
        return []
    return _parse_chart(res.data, max_rows=max_rows)


def fetch_quotes(http: Http, yahoo_symbols: list[str]) -> dict[str, dict]:
    """返回 {yahoo_symbol: {last, asof_ms}}。优先 batch quote，失败则逐个 chart。"""
    out: dict[str, dict] = {}
    if not yahoo_symbols:
        return out
    joined = ",".join(quote(s, safe="^=") for s in yahoo_symbols)
    res = http.get_json(f"{QUOTE}?symbols={joined}")
    if res.ok:
        results = ((res.data or {}).get("quoteResponse") or {}).get("result") or []
        for r in results:
            sym = str(r.get("symbol") or "")
            last = to_num(r.get("regularMarketPrice") or r.get("postMarketPrice") or r.get("preMarketPrice"))
            asof = to_ms(r.get("regularMarketTime"))
            if asof is not None and asof < 1_000_000_000_000:
                asof *= 1000
            # Yahoo regularMarketTime 有时是秒
            if asof is not None and asof < 10_000_000_000:
                asof *= 1000
            if sym and last is not None:
                out[sym] = {"last": last, "asof_ms": asof or int(time.time() * 1000)}
        if len(out) >= max(1, len(yahoo_symbols) // 2):
            return out
    # fallback：逐 symbol 用 chart meta（更慢但稳）
    for sym in yahoo_symbols:
        if sym in out:
            continue
        one = _quote_via_chart(http, sym)
        if one:
            out[sym] = one
    return out


def fetch_session_quotes(http: Http, yahoo_symbols: list[str]) -> dict[str, dict]:
    """正股常规交易时段的涨跌幅。只认 regularMarket，不含盘前盘后。

    返回 {symbol: {last, change_pct, asof_ms, regular}}。
    change_pct 是相对前收盘的百分数。不在常规时段的代码不出现。
    """
    out: dict[str, dict] = {}
    if not yahoo_symbols:
        return out
    joined = ",".join(quote(s, safe="^=") for s in yahoo_symbols)
    res = http.get_json(f"{QUOTE}?symbols={joined}")
    if res.ok:
        results = ((res.data or {}).get("quoteResponse") or {}).get("result") or []
        for row in results:
            parsed = _session_from_quote(row)
            if parsed:
                out[parsed[0]] = parsed[1]
    for sym in yahoo_symbols:
        if sym in out:
            continue
        one = _session_via_chart(http, sym)
        if one:
            out[sym] = one
    return out


def _session_from_quote(row: dict) -> tuple[str, dict] | None:
    sym = str(row.get("symbol") or "")
    if str(row.get("marketState") or "") != "REGULAR":
        return None
    last = to_num(row.get("regularMarketPrice"))
    prev = to_num(row.get("regularMarketPreviousClose"))
    change = to_num(row.get("regularMarketChangePercent"))
    asof = to_ms(row.get("regularMarketTime"))
    if change is None and last is not None and prev not in (None, 0):
        change = (last - prev) / prev * 100.0
    if not sym or last is None or change is None or asof is None:
        return None
    return sym, {"last": last, "change_pct": change, "asof_ms": asof, "regular": True}


def _session_via_chart(http: Http, yahoo_symbol: str) -> dict | None:
    sym = quote(yahoo_symbol, safe="^=")
    res = http.get_json(QUOTE_CHART.format(symbol=sym))
    if not res.ok:
        return None
    chart = ((res.data or {}).get("chart") or {}).get("result") or []
    if not chart:
        return None
    meta = chart[0].get("meta") or {}
    last = to_num(meta.get("regularMarketPrice"))
    prev = to_num(meta.get("chartPreviousClose") or meta.get("previousClose"))
    asof = to_ms(meta.get("regularMarketTime"))
    period = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    start = to_ms(period.get("start"))
    end = to_ms(period.get("end"))
    regular = (
        start is not None
        and end is not None
        and asof is not None
        and start <= asof < end
    )
    if not regular or last is None or prev in (None, 0) or asof is None:
        return None
    return {"last": last, "change_pct": (last - prev) / prev * 100.0, "asof_ms": asof, "regular": True}


def _quote_via_chart(http: Http, yahoo_symbol: str) -> dict | None:
    sym = quote(yahoo_symbol, safe="^=")
    res = http.get_json(QUOTE_CHART.format(symbol=sym))
    if not res.ok:
        return None
    chart = ((res.data or {}).get("chart") or {}).get("result") or []
    if not chart:
        return None
    meta = chart[0].get("meta") or {}
    last = to_num(meta.get("regularMarketPrice") or meta.get("previousClose"))
    asof = to_ms(meta.get("regularMarketTime"))
    if asof is not None and asof < 10_000_000_000:
        asof *= 1000
    if last is None:
        return None
    return {"last": last, "asof_ms": asof or int(time.time() * 1000)}


def _parse_chart(data: Any, *, max_rows: int) -> list[dict]:
    chart = ((data or {}).get("chart") or {}).get("result") or []
    if not chart:
        return []
    r0 = chart[0]
    ts = r0.get("timestamp") or []
    quote = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    vols = quote.get("volume") or []
    rows: list[dict] = []
    for i, t in enumerate(ts):
        tm = to_ms(t)
        if tm is not None and tm < 10_000_000_000:
            tm *= 1000
        c = to_num(closes[i] if i < len(closes) else None)
        if tm is None or c is None:
            continue
        o = to_num(opens[i] if i < len(opens) else None) or c
        h = to_num(highs[i] if i < len(highs) else None) or c
        l = to_num(lows[i] if i < len(lows) else None) or c
        v = to_num(vols[i] if i < len(vols) else None)
        vol_usd = (v * c) if v is not None else None
        rows.append({
            "time": tm,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume_usd": vol_usd,
        })
    rows.sort(key=lambda x: x["time"])
    if len(rows) > max_rows:
        rows = rows[-max_rows:]
    return rows
