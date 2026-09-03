"""10 家交易所原生 API 适配器（阶段一：永续 price + OI + funding）。

每个适配器提供：
  discover(http)               -> list[Instrument]
  fetch_price(http, inst, iv)  -> list[{time, open, high, low, close, volume_usd}]
  fetch_oi(http, inst, iv)     -> list[{time, oi_usd}]        # 历史或当日快照
  fetch_funding(http, inst)    -> list[{time, rate}]

volume_usd 优先用交易所返回的计价币成交额；缺失则用 base_volume * close 估算。
被封锁的交易所（Binance/Bybit）代码就绪，直连失败会被 driver 优雅跳过。
"""
from __future__ import annotations

import time
from typing import Callable

from _common import (
    Http,
    Instrument,
    classify_sector,
    normalize_base,
    to_ms,
    to_num,
)

DAY_MS = 86_400_000


def _now_day_ms() -> int:
    return (int(time.time() * 1000) // DAY_MS) * DAY_MS


def _mk_price_row(t, o, h, l, c, vol_usd, *, estimated: bool = False) -> dict | None:
    tm = to_ms(t)
    cn = to_num(c)
    if tm is None or cn is None:
        return None
    row = {
        "time": tm,
        "open": to_num(o),
        "high": to_num(h),
        "low": to_num(l),
        "close": cn,
        "volume_usd": to_num(vol_usd) or 0.0,
    }
    if estimated:
        row["volume_estimated"] = True
    return row


# ═══════════════════════════ OKX ═══════════════════════════


class OKX:
    name = "OKX"
    base = "https://www.okx.com"
    _bar = {"1h": "1H", "4h": "4H", "1d": "1D"}

    def discover(self, http: Http) -> list[Instrument]:
        out: list[Instrument] = []
        res = http.get_json(f"{self.base}/api/v5/public/instruments?instType=SWAP")
        if not res.ok:
            return out
        for r in res.data.get("data") or []:
            cat = str(r.get("instCategory") or "")
            base = r.get("settleCcy") or ""
            inst_id = r.get("instId") or ""
            base_asset = (inst_id.split("-")[0] if inst_id else "").upper()
            native_stock = cat == "3"
            if cat not in ("3", "4"):  # 3=Stocks 4=Commodities（含指数 SPX 亦常为 3/4）
                # 仍尝试按 base 判定指数/商品
                if classify_sector(base_asset) is None:
                    continue
            sector = classify_sector(base_asset, native_stock=native_stock)
            if sector is None:
                continue
            out.append(Instrument(self.name, inst_id, base_asset, sector, "perp",
                                   r.get("ctValCcy") or "USDT"))
        return out

    def fetch_price(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        bar = self._bar[interval]
        rows: dict[int, dict] = {}
        after = ""
        for _ in range(60):
            url = f"{self.base}/api/v5/market/history-candles?instId={inst.instrument_id}&bar={bar}&limit=100"
            if after:
                url += f"&after={after}"
            res = http.get_json(url)
            if not res.ok:
                break
            data = res.data.get("data") or []
            if not data:
                break
            for c in data:
                row = _mk_price_row(c[0], c[1], c[2], c[3], c[4], c[7])
                if row:
                    rows[row["time"]] = row
            after = str(data[-1][0])
            if len(data) < 100:
                break
        return [rows[t] for t in sorted(rows)]

    def fetch_oi(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        res = http.get_json(f"{self.base}/api/v5/public/open-interest?instId={inst.instrument_id}")
        if not res.ok or not (res.data.get("data") or []):
            return []
        d = res.data["data"][0]
        usd = to_num(d.get("oiUsd"))
        if usd is None:
            return []
        return [{"time": _now_day_ms(), "oi_usd": usd}]

    def fetch_funding(self, http: Http, inst: Instrument) -> list[dict]:
        rows: dict[int, dict] = {}
        after = ""
        for _ in range(20):
            url = f"{self.base}/api/v5/public/funding-rate-history?instId={inst.instrument_id}&limit=100"
            if after:
                url += f"&after={after}"
            res = http.get_json(url)
            if not res.ok:
                break
            data = res.data.get("data") or []
            if not data:
                break
            for d in data:
                tm = to_ms(d.get("fundingTime"))
                rate = to_num(d.get("realizedRate") or d.get("fundingRate"))
                if tm is not None and rate is not None:
                    rows[tm] = {"time": tm, "rate": rate}
            after = str(data[-1]["fundingTime"])
            if len(data) < 100:
                break
        return [rows[t] for t in sorted(rows)]


# ═══════════════════════════ Gate ═══════════════════════════


class Gate:
    name = "Gate"
    base = "https://api.gateio.ws/api/v4"
    _iv = {"1h": "1h", "4h": "4h", "1d": "1d"}

    def discover(self, http: Http) -> list[Instrument]:
        out: list[Instrument] = []
        res = http.get_json(f"{self.base}/futures/usdt/contracts?limit=1000")
        if not res.ok:
            return out
        for r in res.data if isinstance(res.data, list) else []:
            name = r.get("name") or ""  # e.g. XAU_USDT, TSLA_USDT
            base_asset = name.split("_")[0].upper()
            native_stock = base_asset.endswith("STOCK") or "STOCK" in base_asset
            sector = classify_sector(base_asset, native_stock=native_stock)
            if sector is None:
                continue
            out.append(Instrument(self.name, name, base_asset, sector, "perp", "USDT"))
        return out

    def fetch_price(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        iv = self._iv[interval]
        rows: dict[int, dict] = {}
        to_ts = int(time.time())
        for _ in range(30):
            url = f"{self.base}/futures/usdt/candlesticks?contract={inst.instrument_id}&interval={iv}&limit=1999&to={to_ts}"
            res = http.get_json(url)
            if not res.ok or not isinstance(res.data, list) or not res.data:
                break
            earliest = None
            for c in res.data:
                row = _mk_price_row(c.get("t"), c.get("o"), c.get("h"), c.get("l"), c.get("c"), c.get("sum"))
                if row:
                    rows[row["time"]] = row
                t = to_num(c.get("t"))
                if t is not None:
                    earliest = t if earliest is None else min(earliest, t)
            if earliest is None or len(res.data) < 1999:
                break
            to_ts = int(earliest) - 1
        return [rows[t] for t in sorted(rows)]

    def fetch_oi(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        iv = self._iv[interval]
        url = f"{self.base}/futures/usdt/contract_stats?contract={inst.instrument_id}&interval={iv}&limit=1000"
        res = http.get_json(url)
        if not res.ok or not isinstance(res.data, list):
            return []
        out = []
        for d in res.data:
            tm = to_ms(d.get("time"))
            usd = to_num(d.get("open_interest_usd"))
            if tm is not None and usd is not None:
                out.append({"time": tm, "oi_usd": usd})
        return out

    def fetch_funding(self, http: Http, inst: Instrument) -> list[dict]:
        res = http.get_json(f"{self.base}/futures/usdt/funding_rate?contract={inst.instrument_id}&limit=1000")
        if not res.ok or not isinstance(res.data, list):
            return []
        out = []
        for d in res.data:
            tm = to_ms(d.get("t"))
            rate = to_num(d.get("r"))
            if tm is not None and rate is not None:
                out.append({"time": tm, "rate": rate})
        return out


# ═══════════════════════════ Bitget ═══════════════════════════


class Bitget:
    name = "Bitget"
    base = "https://api.bitget.com/api/v2/mix/market"
    _g = {"1h": "1H", "4h": "4H", "1d": "1D"}

    def discover(self, http: Http) -> list[Instrument]:
        out: list[Instrument] = []
        res = http.get_json(f"{self.base}/contracts?productType=USDT-FUTURES")
        if not res.ok:
            return out
        for r in res.data.get("data") or []:
            sym = r.get("symbol") or ""
            base_asset = (r.get("baseCoin") or "").upper()
            is_rwa = str(r.get("isRwa") or "").upper() == "YES"
            native_stock = is_rwa or base_asset.endswith("STOCK") or "STOCK" in base_asset
            sector = classify_sector(base_asset, native_stock=native_stock)
            if sector is None:
                continue
            out.append(Instrument(self.name, sym, base_asset, sector, "perp", "USDT"))
        return out

    def fetch_price(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        g = self._g[interval]
        rows: dict[int, dict] = {}
        end = ""
        for _ in range(30):
            url = (f"https://api.bitget.com/api/v2/mix/market/history-candles?symbol={inst.instrument_id}"
                   f"&productType=USDT-FUTURES&granularity={g}&limit=200")
            if end:
                url += f"&endTime={end}"
            res = http.get_json(url)
            if not res.ok or not (res.data.get("data") or []):
                break
            data = res.data["data"]
            earliest = None
            for c in data:
                row = _mk_price_row(c[0], c[1], c[2], c[3], c[4], c[6] if len(c) > 6 else None)
                if row:
                    rows[row["time"]] = row
                t = to_num(c[0])
                if t is not None:
                    earliest = t if earliest is None else min(earliest, t)
            if earliest is None or len(data) < 200:
                break
            end = str(int(earliest) - 1)
        return [rows[t] for t in sorted(rows)]

    def fetch_oi(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        res = http.get_json(f"{self.base}/open-interest?symbol={inst.instrument_id}&productType=USDT-FUTURES")
        if not res.ok:
            return []
        lst = (res.data.get("data") or {}).get("openInterestList") or []
        if not lst:
            return []
        size = to_num(lst[0].get("size"))
        if size is None:
            return []
        # size 为 base 数量，需乘最新价估算 usd（交给 driver 用 price 末值补；此处先存 size）
        return [{"time": _now_day_ms(), "oi_base": size}]

    def fetch_funding(self, http: Http, inst: Instrument) -> list[dict]:
        rows: dict[int, dict] = {}
        for page in range(1, 11):
            url = (f"{self.base}/history-fund-rate?symbol={inst.instrument_id}"
                   f"&productType=USDT-FUTURES&pageSize=100&pageNo={page}")
            res = http.get_json(url)
            if not res.ok or not (res.data.get("data") or []):
                break
            data = res.data["data"]
            for d in data:
                tm = to_ms(d.get("fundingTime"))
                rate = to_num(d.get("fundingRate"))
                if tm is not None and rate is not None:
                    rows[tm] = {"time": tm, "rate": rate}
            if len(data) < 100:
                break
        return [rows[t] for t in sorted(rows)]


# ═══════════════════════════ MEXC ═══════════════════════════


class MEXC:
    name = "MEXC"
    hosts = ("https://futures.mexc.com", "https://contract.mexc.com")
    _iv = {"1h": "Min60", "4h": "Hour4", "1d": "Day1"}

    def _urls(self, path: str) -> list[str]:
        return [h + path for h in self.hosts]

    def discover(self, http: Http) -> list[Instrument]:
        out: list[Instrument] = []
        res = http.get_json_multi(self._urls("/api/v1/contract/detail"))
        if not res.ok:
            return out
        for r in res.data.get("data") or []:
            sym = r.get("symbol") or ""
            base_asset = (r.get("baseCoin") or sym.split("_")[0]).upper()
            native_stock = base_asset.endswith("STOCK") or "STOCK" in base_asset
            sector = classify_sector(base_asset, native_stock=native_stock)
            if sector is None:
                continue
            out.append(Instrument(self.name, sym, base_asset, sector, "perp",
                                   (r.get("quoteCoin") or "USDT")))
        return out

    def fetch_price(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        iv = self._iv[interval]
        res = http.get_json_multi(self._urls(
            f"/api/v1/contract/kline/{inst.instrument_id}?interval={iv}"))
        if not res.ok:
            return []
        d = res.data.get("data") or {}
        t = d.get("time") or []
        o, h, l, c = d.get("open") or [], d.get("high") or [], d.get("low") or [], d.get("close") or []
        amt = d.get("amount") or []  # 计价币成交额
        out = []
        for i in range(len(t)):
            row = _mk_price_row(t[i], o[i], h[i], l[i], c[i], amt[i] if i < len(amt) else None)
            if row:
                out.append(row)
        return out

    def fetch_oi(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        res = http.get_json_multi(self._urls(f"/api/v1/contract/openInterest/{inst.instrument_id}"))
        if not res.ok:
            return []
        d = res.data.get("data") or {}
        base = to_num(d.get("holdVol") if isinstance(d, dict) else None)
        if base is None:
            return []
        return [{"time": _now_day_ms(), "oi_base": base}]

    def fetch_funding(self, http: Http, inst: Instrument) -> list[dict]:
        rows: dict[int, dict] = {}
        for page in range(1, 11):
            res = http.get_json_multi(self._urls(
                f"/api/v1/contract/funding_rate/history?symbol={inst.instrument_id}&page_num={page}&page_size=100"))
            if not res.ok:
                break
            lst = ((res.data.get("data") or {}).get("resultList")) or []
            if not lst:
                break
            for d in lst:
                tm = to_ms(d.get("settleTime"))
                rate = to_num(d.get("fundingRate"))
                if tm is not None and rate is not None:
                    rows[tm] = {"time": tm, "rate": rate}
            if len(lst) < 100:
                break
        return [rows[t] for t in sorted(rows)]


# ═══════════════════════════ HTX ═══════════════════════════


class HTX:
    name = "HTX"
    hosts = ("https://api.hbdm.vn", "https://api.hbdm.com")
    _p = {"1h": "60min", "4h": "4hour", "1d": "1day"}

    def _urls(self, path: str) -> list[str]:
        return [h + path for h in self.hosts]

    def discover(self, http: Http) -> list[Instrument]:
        out: list[Instrument] = []
        res = http.get_json_multi(self._urls("/linear-swap-api/v1/swap_contract_info"))
        if not res.ok:
            return out
        for r in res.data.get("data") or []:
            code = r.get("contract_code") or ""
            base_asset = (code.split("-")[0]).upper()
            native_stock = base_asset.endswith("STOCK") or "STOCK" in base_asset
            sector = classify_sector(base_asset, native_stock=native_stock)
            if sector is None:
                continue
            out.append(Instrument(self.name, code, base_asset, sector, "perp", "USDT"))
        return out

    def fetch_price(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        p = self._p[interval]
        res = http.get_json_multi(self._urls(
            f"/linear-swap-ex/market/history/kline?contract_code={inst.instrument_id}&period={p}&size=2000"))
        if not res.ok:
            return []
        out = []
        for c in res.data.get("data") or []:
            row = _mk_price_row(c.get("id"), c.get("open"), c.get("high"), c.get("low"),
                                c.get("close"), c.get("trade_turnover"))
            if row:
                out.append(row)
        return out

    def fetch_oi(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        res = http.get_json_multi(self._urls(
            f"/linear-swap-api/v1/swap_open_interest?contract_code={inst.instrument_id}"))
        if not res.ok or not (res.data.get("data") or []):
            return []
        d = res.data["data"][0]
        usd = to_num(d.get("value"))
        if usd is not None:
            return [{"time": _now_day_ms(), "oi_usd": usd}]
        base = to_num(d.get("volume"))
        return [{"time": _now_day_ms(), "oi_base": base}] if base is not None else []

    def fetch_funding(self, http: Http, inst: Instrument) -> list[dict]:
        rows: dict[int, dict] = {}
        for page in range(1, 11):
            res = http.get_json_multi(self._urls(
                f"/linear-swap-api/v1/swap_historical_funding_rate?contract_code={inst.instrument_id}&page_size=50&page_index={page}"))
            if not res.ok:
                break
            data = ((res.data.get("data") or {}).get("data")) or []
            if not data:
                break
            for d in data:
                tm = to_ms(d.get("funding_time"))
                rate = to_num(d.get("funding_rate"))
                if tm is not None and rate is not None:
                    rows[tm] = {"time": tm, "rate": rate}
            if len(data) < 50:
                break
        return [rows[t] for t in sorted(rows)]


# ═══════════════════════════ Crypto.com ═══════════════════════════


class CryptoCom:
    name = "Crypto.com"
    base = "https://api.crypto.com/exchange/v1"
    _tf = {"1h": "1h", "4h": "4h", "1d": "1D"}

    def discover(self, http: Http) -> list[Instrument]:
        out: list[Instrument] = []
        res = http.get_json(f"{self.base}/public/get-instruments")
        if not res.ok:
            return out
        for r in (res.data.get("result") or {}).get("data") or []:
            sym = r.get("symbol") or ""
            if not sym.endswith("-PERP"):
                continue
            base_asset = sym.replace("-PERP", "")
            for q in ("USD", "USDT"):
                if base_asset.endswith(q) and len(base_asset) > len(q):
                    base_asset = base_asset[: -len(q)]
                    break
            base_asset = base_asset.upper()
            native_stock = base_asset.endswith("STOCK") or "STOCK" in base_asset
            sector = classify_sector(base_asset, native_stock=native_stock)
            if sector is None:
                continue
            out.append(Instrument(self.name, sym, base_asset, sector, "perp", "USD"))
        return out

    def fetch_price(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        tf = self._tf[interval]
        res = http.get_json(
            f"{self.base}/public/get-candlestick?instrument_name={inst.instrument_id}&timeframe={tf}&count=1000")
        if not res.ok:
            return []
        out = []
        for c in (res.data.get("result") or {}).get("data") or []:
            close = to_num(c.get("c"))
            vol_base = to_num(c.get("v"))
            vol_usd = (close * vol_base) if (close is not None and vol_base is not None) else None
            row = _mk_price_row(c.get("t"), c.get("o"), c.get("h"), c.get("l"), c.get("c"),
                                vol_usd, estimated=True)
            if row:
                out.append(row)
        return out

    def fetch_oi(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        res = http.get_json(
            f"{self.base}/public/get-valuations?instrument_name={inst.instrument_id}&valuation_type=open_interest&count=100")
        if not res.ok:
            return []
        out = []
        for d in (res.data.get("result") or {}).get("data") or []:
            tm = to_ms(d.get("t"))
            val = to_num(d.get("v"))
            if tm is not None and val is not None:
                out.append({"time": tm, "oi_usd": val})
        return out

    def fetch_funding(self, http: Http, inst: Instrument) -> list[dict]:
        res = http.get_json(
            f"{self.base}/public/get-valuations?instrument_name={inst.instrument_id}&valuation_type=funding_hist&count=500")
        if not res.ok:
            return []
        out = []
        for d in (res.data.get("result") or {}).get("data") or []:
            tm = to_ms(d.get("t"))
            rate = to_num(d.get("v"))
            if tm is not None and rate is not None:
                out.append({"time": tm, "rate": rate})
        return out


# ═══════════════════════════ Coinbase INTX ═══════════════════════════


class Coinbase:
    name = "Coinbase"
    base = "https://api.international.coinbase.com/api/v1"
    _g = {"1h": "ONE_HOUR", "4h": "FOUR_HOUR", "1d": "ONE_DAY"}
    _step = {"1h": 3600, "4h": 14400, "1d": 86400}

    def __init__(self) -> None:
        self._oi_snapshot: dict[str, dict] = {}

    def discover(self, http: Http) -> list[Instrument]:
        out: list[Instrument] = []
        res = http.get_json(f"{self.base}/instruments")
        if not res.ok or not isinstance(res.data, list):
            return out
        for r in res.data:
            if (r.get("type") or "") != "PERP":
                continue
            sym = r.get("symbol") or ""
            base_asset = (r.get("base_asset_name") or sym.replace("-PERP", "")).upper()
            native_stock = base_asset.endswith("STOCK") or "STOCK" in base_asset
            # Coinbase 个股用真实 ticker（无 marker），此处对未知 ticker 也视为潜在股票：
            sector = classify_sector(base_asset, native_stock=native_stock)
            if sector is None:
                # 排除主流 crypto，其余 -PERP 若非商品/指数/外汇，跳过（Coinbase 个股需白名单）
                continue
            inst = Instrument(self.name, sym, base_asset, sector, "perp", "USDC")
            oi = to_num(r.get("open_interest"))
            mark = to_num((r.get("quote") or {}).get("mark_price"))
            if oi is not None and mark is not None:
                self._oi_snapshot[sym] = {"oi_usd": oi * mark}
            out.append(inst)
        return out

    def fetch_price(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        g = self._g[interval]
        step = self._step[interval]
        rows: dict[int, dict] = {}
        end = int(time.time())
        for _ in range(30):
            start = end - step * 300
            url = (f"{self.base}/instruments/{inst.instrument_id}/candles"
                   f"?granularity={g}&start={start}&end={end}")
            res = http.get_json(url)
            if not res.ok:
                break
            data = res.data if isinstance(res.data, list) else (res.data.get("candles") or res.data.get("aggregations") or [])
            if not data:
                break
            got = 0
            for c in data:
                if isinstance(c, dict):
                    close = to_num(c.get("close"))
                    vol = to_num(c.get("volume"))
                    vol_usd = (close * vol) if (close is not None and vol is not None) else None
                    row = _mk_price_row(c.get("start") or c.get("time"), c.get("open"),
                                        c.get("high"), c.get("low"), c.get("close"),
                                        vol_usd, estimated=True)
                else:
                    close = to_num(c[4])
                    vol = to_num(c[5]) if len(c) > 5 else None
                    vol_usd = (close * vol) if (close is not None and vol is not None) else None
                    row = _mk_price_row(c[0], c[1], c[2], c[3], c[4], vol_usd, estimated=True)
                if row:
                    rows[row["time"]] = row
                    got += 1
            end = start - 1
            if got == 0:
                break
        return [rows[t] for t in sorted(rows)]

    def fetch_oi(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        snap = self._oi_snapshot.get(inst.instrument_id)
        if not snap:
            return []
        return [{"time": _now_day_ms(), "oi_usd": snap["oi_usd"]}]

    def fetch_funding(self, http: Http, inst: Instrument) -> list[dict]:
        return []


# ═══════════════════════════ Kraken Futures ═══════════════════════════


class Kraken:
    name = "Kraken"
    base = "https://futures.kraken.com"
    _res = {"1h": "1h", "4h": "4h", "1d": "1d"}

    def __init__(self) -> None:
        self._tick: dict[str, dict] = {}

    def discover(self, http: Http) -> list[Instrument]:
        out: list[Instrument] = []
        res = http.get_json(f"{self.base}/derivatives/api/v3/instruments")
        if not res.ok:
            return out
        # 预取 tickers 拿 OI / funding 快照
        tk = http.get_json(f"{self.base}/derivatives/api/v3/tickers")
        if tk.ok:
            for t in tk.data.get("tickers") or []:
                self._tick[str(t.get("symbol") or "").upper()] = t
        for r in res.data.get("instruments") or []:
            sym = str(r.get("symbol") or "")
            if not sym.upper().startswith("PF_"):  # 只取永续
                continue
            core = sym.upper()[3:]  # 去掉 PF_
            for q in ("USD", "USDT"):
                if core.endswith(q) and len(core) > len(q):
                    core = core[: -len(q)]
                    break
            base_asset = core
            native_stock = base_asset.endswith("STOCK") or "STOCK" in base_asset
            sector = classify_sector(base_asset, native_stock=native_stock)
            if sector is None:
                continue
            out.append(Instrument(self.name, sym, base_asset, sector, "perp", "USD"))
        return out

    def fetch_price(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        r = self._res[interval]
        res = http.get_json(f"{self.base}/api/charts/v1/trade/{inst.instrument_id}/{r}")
        if not res.ok:
            return []
        out = []
        for c in res.data.get("candles") or []:
            close = to_num(c.get("close"))
            vol = to_num(c.get("volume"))
            vol_usd = (close * vol) if (close is not None and vol is not None) else None
            row = _mk_price_row(c.get("time"), c.get("open"), c.get("high"), c.get("low"),
                                c.get("close"), vol_usd, estimated=True)
            if row:
                out.append(row)
        return out

    def fetch_oi(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        t = self._tick.get(inst.instrument_id.upper())
        if not t:
            return []
        oi = to_num(t.get("openInterest"))
        mark = to_num(t.get("markPrice"))
        if oi is None or mark is None:
            return []
        return [{"time": _now_day_ms(), "oi_usd": oi * mark}]

    def fetch_funding(self, http: Http, inst: Instrument) -> list[dict]:
        res = http.get_json(
            f"{self.base}/derivatives/api/v4/historicalfundingrates?symbol={inst.instrument_id}")
        if not res.ok:
            return []
        out = []
        for d in res.data.get("rates") or []:
            tm = to_ms(d.get("timestamp"))
            rate = to_num(d.get("relativeFundingRate") if d.get("relativeFundingRate") is not None else d.get("fundingRate"))
            if tm is not None and rate is not None:
                out.append({"time": tm, "rate": rate})
        return out


# ═══════════════════════════ Bybit（被封，代码就绪）═══════════════════════════


class Bybit:
    name = "Bybit"
    base = "https://api.bybit.com"
    _iv = {"1h": "60", "4h": "240", "1d": "D"}
    _oiv = {"1h": "1h", "4h": "4h", "1d": "1d"}

    def discover(self, http: Http) -> list[Instrument]:
        out: list[Instrument] = []
        cursor = ""
        for _ in range(10):
            url = f"{self.base}/v5/market/instruments-info?category=linear&limit=1000"
            if cursor:
                url += f"&cursor={cursor}"
            res = http.get_json(url)
            if not res.ok:
                break
            result = res.data.get("result") or {}
            for r in result.get("list") or []:
                sym = r.get("symbol") or ""
                st = (r.get("symbolType") or "").lower()
                base_asset = (r.get("baseCoin") or "").upper()
                native_stock = st == "stock" or base_asset.endswith("STOCK") or "STOCK" in base_asset
                if st not in ("stock", "commodity") and classify_sector(base_asset) is None:
                    continue
                sector = classify_sector(base_asset, native_stock=native_stock)
                if sector is None:
                    continue
                out.append(Instrument(self.name, sym, base_asset, sector, "perp", "USDT"))
            cursor = result.get("nextPageCursor") or ""
            if not cursor:
                break
        return out

    def fetch_price(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        iv = self._iv[interval]
        rows: dict[int, dict] = {}
        end = int(time.time() * 1000)
        for _ in range(30):
            url = (f"{self.base}/v5/market/kline?category=linear&symbol={inst.instrument_id}"
                   f"&interval={iv}&limit=1000&end={end}")
            res = http.get_json(url)
            if not res.ok:
                break
            lst = (res.data.get("result") or {}).get("list") or []
            if not lst:
                break
            earliest = None
            for c in lst:
                row = _mk_price_row(c[0], c[1], c[2], c[3], c[4], c[6] if len(c) > 6 else None)
                if row:
                    rows[row["time"]] = row
                t = to_num(c[0])
                if t is not None:
                    earliest = t if earliest is None else min(earliest, t)
            if earliest is None or len(lst) < 1000:
                break
            end = int(earliest) - 1
        return [rows[t] for t in sorted(rows)]

    def fetch_oi(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        iv = self._oiv[interval]
        url = (f"{self.base}/v5/market/open-interest?category=linear&symbol={inst.instrument_id}"
               f"&intervalTime={iv}&limit=200")
        res = http.get_json(url)
        if not res.ok:
            return []
        out = []
        for d in (res.data.get("result") or {}).get("list") or []:
            tm = to_ms(d.get("timestamp"))
            base = to_num(d.get("openInterest"))
            if tm is not None and base is not None:
                out.append({"time": tm, "oi_base": base})
        return out

    def fetch_funding(self, http: Http, inst: Instrument) -> list[dict]:
        url = f"{self.base}/v5/market/funding/history?category=linear&symbol={inst.instrument_id}&limit=200"
        res = http.get_json(url)
        if not res.ok:
            return []
        out = []
        for d in (res.data.get("result") or {}).get("list") or []:
            tm = to_ms(d.get("fundingRateTimestamp"))
            rate = to_num(d.get("fundingRate"))
            if tm is not None and rate is not None:
                out.append({"time": tm, "rate": rate})
        return out


# ═══════════════════════════ Binance（被封，代码就绪）═══════════════════════════


class Binance:
    name = "Binance"
    base = "https://fapi.binance.com"
    _iv = {"1h": "1h", "4h": "4h", "1d": "1d"}

    def discover(self, http: Http) -> list[Instrument]:
        out: list[Instrument] = []
        res = http.get_json(f"{self.base}/fapi/v1/exchangeInfo")
        if not res.ok:
            return out
        for r in res.data.get("symbols") or []:
            if (r.get("contractType") or "") != "PERPETUAL":
                continue
            base_asset = (r.get("baseAsset") or "").upper()
            sym = r.get("symbol") or ""
            native_stock = base_asset.endswith("STOCK") or "STOCK" in base_asset
            sector = classify_sector(base_asset, native_stock=native_stock)
            if sector is None:
                continue
            out.append(Instrument(self.name, sym, base_asset, sector, "perp", "USDT"))
        return out

    def fetch_price(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        iv = self._iv[interval]
        rows: dict[int, dict] = {}
        end = int(time.time() * 1000)
        for _ in range(30):
            url = f"{self.base}/fapi/v1/klines?symbol={inst.instrument_id}&interval={iv}&limit=1500&endTime={end}"
            res = http.get_json(url)
            if not res.ok or not isinstance(res.data, list) or not res.data:
                break
            earliest = None
            for c in res.data:
                row = _mk_price_row(c[0], c[1], c[2], c[3], c[4], c[7])
                if row:
                    rows[row["time"]] = row
                t = to_num(c[0])
                if t is not None:
                    earliest = t if earliest is None else min(earliest, t)
            if earliest is None or len(res.data) < 1500:
                break
            end = int(earliest) - 1
        return [rows[t] for t in sorted(rows)]

    def fetch_oi(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        p = {"1h": "1h", "4h": "4h", "1d": "1d"}[interval]
        url = f"{self.base}/futures/data/openInterestHist?symbol={inst.instrument_id}&period={p}&limit=500"
        res = http.get_json(url)
        if not res.ok or not isinstance(res.data, list):
            return []
        out = []
        for d in res.data:
            tm = to_ms(d.get("timestamp"))
            usd = to_num(d.get("sumOpenInterestValue"))
            if tm is not None and usd is not None:
                out.append({"time": tm, "oi_usd": usd})
        return out

    def fetch_funding(self, http: Http, inst: Instrument) -> list[dict]:
        rows: dict[int, dict] = {}
        end = int(time.time() * 1000)
        for _ in range(10):
            url = f"{self.base}/fapi/v1/fundingRate?symbol={inst.instrument_id}&limit=1000&endTime={end}"
            res = http.get_json(url)
            if not res.ok or not isinstance(res.data, list) or not res.data:
                break
            earliest = None
            for d in res.data:
                tm = to_ms(d.get("fundingTime"))
                rate = to_num(d.get("fundingRate"))
                if tm is not None and rate is not None:
                    rows[tm] = {"time": tm, "rate": rate}
                    earliest = tm if earliest is None else min(earliest, tm)
            if earliest is None or len(res.data) < 1000:
                break
            end = earliest - 1
        return [rows[t] for t in sorted(rows)]


# 是否有"历史 OI"可回补（按 hist_only 决策：仅这些交易所产出 OI，快照型不产出）
Gate.oi_history = True
CryptoCom.oi_history = True
Bybit.oi_history = True
Binance.oi_history = True

ALL_ADAPTERS: list = [
    OKX(), Gate(), Bitget(), MEXC(), HTX(), CryptoCom(), Coinbase(), Kraken(), Bybit(), Binance(),
]


def get_adapters(names: list[str] | None = None) -> list:
    if not names:
        return ALL_ADAPTERS
    want = {n.lower() for n in names}
    return [a for a in ALL_ADAPTERS if a.name.lower() in want]
