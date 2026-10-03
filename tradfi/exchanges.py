"""11 家交易所原生 API 适配器（阶段一：永续 price + OI + funding）。

含 10 所 CEX + Hyperliquid HIP-3 TradFi 直连。HL crypto 永续走 Coinglass，不在此模块。

每个适配器提供：
  discover(http)               -> list[Instrument]
  fetch_price(http, inst, iv)  -> list[{time, open, high, low, close, volume_usd}]
  fetch_oi(http, inst, iv)     -> list[{time, oi_usd}]        # 历史或当日快照
  fetch_funding(http, inst)    -> list[{time, rate}]

volume_usd 优先用交易所返回的计价币成交额；缺失则用 base_volume * close 估算。
被封锁的交易所（Binance/Bybit）代码就绪，直连失败会被 driver 优雅跳过。
"""
from __future__ import annotations

import calendar
import time
from typing import Any, Callable

from _common import (
    COMMODITY_BASES,
    DEFAULT_MAX_DAILY_ROWS,
    INDEX_BASES,
    Http,
    Instrument,
    classify_sector,
    is_crypto_base,
    normalize_base,
    strip_stock_suffix,
    to_ms,
    to_num,
)

DAY_MS = 86_400_000

# 跨所原生股票 ticker 白名单(driver 第1轮发现后注入):用于给【无原生股票标记】的
# 交易所(如 HTX)兜底识别个股。空集时不生效。自更新:别家原生确认的股票会自动扩充。
CROSS_STOCK_WHITELIST: set[str] = set()


def _tradfi_sector(base_raw: str, kind: str | None = None) -> str | None:
    """在【已确认是 tradfi】的前提下定 Stocks/Commodities/Indices;撞名加密返回 None(跳过)。

    kind 来自交易所原生类别(stock/commodity/index);为 None 时纯按 base 集合路由。
    指数型 ETF(QQQ/SPY/IWM 等)即使原生标记为股票也归 Indices,与看板分桶一致。
    CRYPTO_OVERRIDE(SPX6900/PAXG/XAUT 等)即使某所原生标成 commodity/index 也强制排除,
    统一"这些是加密"的口径(不错杀)。
    """
    b = normalize_base(strip_stock_suffix(base_raw))
    if is_crypto_base(base_raw):
        return None
    if kind == "commodity":
        return "Commodities"
    if kind == "index":
        return "Indices"
    if kind == "stock":
        return "Indices" if b in INDEX_BASES else "Stocks"
    if b in COMMODITY_BASES:
        return "Commodities"
    if b in INDEX_BASES:
        return "Indices"
    return "Stocks"


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
            inst_id = r.get("instId") or ""
            base_asset = (inst_id.split("-")[0] if inst_id else "").upper()
            # OKX 原生:instCategory 3=股票/ETF, 4=大宗;其余(1=加密等)一律非 tradfi。
            # 不再用 base 集合兜底,避免把 SPX6900 等加密提拔成 Indices。
            if cat == "3":
                sector = _tradfi_sector(base_asset, "stock")
            elif cat == "4":
                sector = _tradfi_sector(base_asset, "commodity")
            else:
                continue
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

    # Gate 原生分类在 contract_type 字段(旧代码误读 type='direct' 导致个股全漏)。
    _CT_KIND = {"stocks": "stock", "indices": "index", "commodities": "commodity", "metals": "commodity"}

    def discover(self, http: Http) -> list[Instrument]:
        out: list[Instrument] = []
        # 注意：Gate 该端点不接受 limit 参数，带上会 HTTP 400；不分页，一次返回全部。
        res = http.get_json(f"{self.base}/futures/usdt/contracts")
        if not res.ok:
            return out
        for r in res.data if isinstance(res.data, list) else []:
            name = r.get("name") or ""  # e.g. XAU_USDT, TSLA_USDT, AAPLX_USDT
            kind = self._CT_KIND.get(str(r.get("contract_type") or ""))
            if kind is None:  # ''(加密)/forex 排除
                continue
            base_asset = name.split("_")[0].upper()
            sector = _tradfi_sector(base_asset, kind)
            if sector is None:  # 撞名加密(PAXG/XAUT 等)排除
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
            native_stock = base_asset.endswith("STOCK") or "STOCK" in base_asset
            # 只认 isRwa=YES 或显式 STOCK 命名;排除加密(修 SPX6900/PAXG 错杀)。
            # isRwa 是布尔不分 sector,故在确认 tradfi 后按 base 集合路由。
            if not (is_rwa or native_stock):
                continue
            sector = _tradfi_sector(base_asset)
            if sector is None:  # 撞名加密(PAXG/XAUT 等)排除
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
        # 旧端点 /api/v1/contract/openInterest/{symbol} 已下线(现 404),
        # holdVol(持仓量,张数)现只在 ticker 端点返回。
        res = http.get_json_multi(self._urls(f"/api/v1/contract/ticker?symbol={inst.instrument_id}"))
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
                # HTX 无原生股票标记:用跨所原生股票白名单兜底(driver 注入)。
                b = normalize_base(strip_stock_suffix(base_asset))
                if b in CROSS_STOCK_WHITELIST and not is_crypto_base(b):
                    sector = "Stocks"
                else:
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

    # Crypto.com 原生分类在 product_type 字段(旧代码靠 base 集合导致个股全漏)。
    _PT_KIND = {"EQUITY": "stock", "PRE_IPO": "stock", "EQUITY_IND": "index", "COMMODITIES": "commodity"}

    def discover(self, http: Http) -> list[Instrument]:
        out: list[Instrument] = []
        res = http.get_json(f"{self.base}/public/get-instruments")
        if not res.ok:
            return out
        for r in (res.data.get("result") or {}).get("data") or []:
            sym = r.get("symbol") or ""
            if not sym.endswith("-PERP"):
                continue
            kind = self._PT_KIND.get(str(r.get("product_type") or ""))
            if kind is None:  # DIGITAL_CURRENCIES 等加密排除
                continue
            base_asset = (r.get("base_ccy") or "").upper()
            sector = _tradfi_sector(base_asset, kind)
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
        # get-valuations 的 valuation_type=open_interest 已失效(HTTP 400,该 API 版本
        # 只剩 mark_price/index_price/funding_hist/estimated_funding_rate)。
        # 持仓量现只能从 get-tickers 的 oi 字段拿到(base 数量快照,与 OKX/HTX 同类)。
        res = http.get_json(f"{self.base}/public/get-tickers?instrument_name={inst.instrument_id}")
        if not res.ok:
            return []
        data = (res.data.get("result") or {}).get("data") or []
        if not data:
            return []
        base = to_num(data[0].get("oi"))
        if base is None:
            return []
        return [{"time": _now_day_ms(), "oi_base": base}]

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

    # Coinbase INTX 原生分类在 underlying_type 字段(和 Binance 类似)。
    # INDEX=COIN50 是加密指数,排除;SPOT 是加密永续,排除。
    _UT_KIND = {"EQUITY": "stock", "EQUITY_ETF": "stock", "PREIPO": "stock", "COMMOD": "commodity"}

    def discover(self, http: Http) -> list[Instrument]:
        out: list[Instrument] = []
        res = http.get_json(f"{self.base}/instruments")
        if not res.ok or not isinstance(res.data, list):
            return out
        for r in res.data:
            if (r.get("type") or "") != "PERP":
                continue
            kind = self._UT_KIND.get(str(r.get("underlying_type") or "").upper())
            if kind is None:
                continue
            sym = r.get("symbol") or ""
            base_asset = (r.get("base_asset_name") or sym.replace("-PERP", "")).upper()
            sector = _tradfi_sector(base_asset, kind)
            if sector is None:
                continue
            inst = Instrument(self.name, sym, base_asset, sector, "perp", "USDC")
            oi = to_num(r.get("open_interest"))
            mark = to_num((r.get("quote") or {}).get("mark_price"))
            if oi is not None and mark is not None:
                self._oi_snapshot[sym] = {"oi_usd": oi * mark}
            out.append(inst)
        return out

    @staticmethod
    def _iso(epoch_s: int) -> str:
        """Coinbase INTX 的 start/end 需 RFC3339（epoch 秒会 HTTP 400）。"""
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(epoch_s)))

    @staticmethod
    def _iso_to_ms(value) -> int | None:
        """把 aggregations[].start 的 ISO 串（'2026-07-12T00:00:00Z'）转 ms。"""
        if not isinstance(value, str) or "T" not in value:
            return to_ms(value)
        try:
            return int(calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ")) * 1000)
        except (ValueError, OverflowError):
            return None

    def fetch_price(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        g = self._g[interval]
        step = self._step[interval]
        rows: dict[int, dict] = {}
        end = int(time.time())
        for _ in range(30):
            start = end - step * 300
            url = (f"{self.base}/instruments/{inst.instrument_id}/candles"
                   f"?granularity={g}&start={self._iso(start)}&end={self._iso(end)}")
            res = http.get_json(url)
            if not res.ok:
                break
            data = res.data if isinstance(res.data, list) else (res.data.get("candles") or res.data.get("aggregations") or [])
            if not data:
                break
            got = 0
            for c in data:
                if isinstance(c, dict):
                    tm = self._iso_to_ms(c.get("start") or c.get("time"))
                    close = to_num(c.get("close"))
                    vol = to_num(c.get("volume"))
                    vol_usd = (close * vol) if (close is not None and vol is not None) else None
                    row = _mk_price_row(tm, c.get("open"),
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

    # Kraken 原生分类在 category 字段(旧代码靠 base 集合+"STOCK"字样,漏光个股)：
    # 真正的个股是 xStocks/Pre-IPO 分类,命名用 "...X" 后缀代币化(如 AAPLX/TSLAX/QQQX),
    # 不是 "STOCK" 字样,故旧逻辑 0 命中。Commodities 分类目前只有 WTIOIL。
    # 其余(Layer 1/DeFi/Meme/...)一律加密,不再用 base 集合兜底(避免误把加密提拔成 tradfi)。
    _CAT_KIND = {"xStocks": "stock", "Pre-IPO": "stock", "Commodities": "commodity"}

    def __init__(self) -> None:
        self._tick: dict[str, dict] = {}

    @staticmethod
    def _strip_x_suffix(base: str) -> str:
        """xStocks/Pre-IPO 命名末尾多一个代币化 'X'(AAPLX→AAPL,QQQX→QQQ,SPCXX→SPCX 例外见下)。

        SPCXX 是 SpaceX 的真实 ticker 本身以 X 结尾(SPCX)+代币化 X，去一层即可；
        真实 ticker 本就以 X 结尾的情形理论上会被去过头，但当前 xStocks/Pre-IPO 名单中
        无此类冲突（逐一核对：GLD/QQQ/SPY/TSLA/NVDA/MSTR/HOOD/GOOGL/CRCL/AAPL/SPCX/
        ANTHROPIC/OPENAI 均不以 X 结尾）。
        """
        return base[:-1] if base.endswith("X") and len(base) > 1 else base

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
            kind = self._CAT_KIND.get(str(r.get("category") or ""))
            if kind is None:  # Forex/加密类目一律排除
                continue
            core = sym.upper()[3:]  # 去掉 PF_
            for q in ("USD", "USDT"):
                if core.endswith(q) and len(core) > len(q):
                    core = core[: -len(q)]
                    break
            if kind == "stock":
                core = self._strip_x_suffix(core)
            sector = _tradfi_sector(core, kind)
            if sector is None:
                continue
            out.append(Instrument(self.name, sym, core, sector, "perp", "USD"))
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

    @staticmethod
    def _iso_to_ms(value) -> int | None:
        """historicalfundingrates 的 timestamp 是 ISO8601('2025-07-13T08:00:00.000Z'),
        to_ms() 只认数字 epoch,直接传会被 to_num() 拒掉返回 None——这是此前 funding 全 0 的真因。
        """
        if not isinstance(value, str) or "T" not in value:
            return to_ms(value)
        try:
            s = value.rstrip("Z").split(".")[0]  # 去掉毫秒与 Z
            return int(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%S")) * 1000)
        except (ValueError, OverflowError):
            return None

    def fetch_funding(self, http: Http, inst: Instrument) -> list[dict]:
        res = http.get_json(
            f"{self.base}/derivatives/api/v4/historicalfundingrates?symbol={inst.instrument_id}")
        if not res.ok:
            return []
        out = []
        for d in res.data.get("rates") or []:
            tm = self._iso_to_ms(d.get("timestamp"))
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
                # Bybit 原生 symbolType:stock / commodity 才是 tradfi;
                # innovation/'' 等一律加密,不再用 base 集合兜底(修 SPX6900/PAXG 错杀)。
                if st == "stock":
                    sector = _tradfi_sector(base_asset, "stock")
                elif st == "commodity":
                    sector = _tradfi_sector(base_asset, "commodity")
                else:
                    continue
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

    # Binance 2025+ 上线股票/大宗/指数永续,用独立合约类型 TRADIFI_PERPETUAL,
    # 且以 underlyingType(EQUITY/COMMODITY/INDEX/KR_EQUITY/PREMARKET)+ underlyingSubType(TradFi)
    # 作原生标记(base 是纯 ticker 如 TSLA/AAPL,不带 STOCK 字样)。
    _CONTRACTS = {"PERPETUAL", "TRADIFI_PERPETUAL"}

    def discover(self, http: Http) -> list[Instrument]:
        out: list[Instrument] = []
        res = http.get_json(f"{self.base}/fapi/v1/exchangeInfo")
        if not res.ok:
            return out
        for r in res.data.get("symbols") or []:
            if (r.get("contractType") or "") not in self._CONTRACTS:
                continue
            if (r.get("status") or "") != "TRADING":
                continue
            base_asset = (r.get("baseAsset") or "").upper()
            sym = r.get("symbol") or ""
            utype = (r.get("underlyingType") or "").upper()
            subs = {str(x).upper() for x in (r.get("underlyingSubType") or [])}
            # 只认真正的 tradfi:股票/大宗类原生类型,或带 TradFi 子标签。
            # 注意 underlyingType=INDEX 是加密指数(BTCDOM/ALL 等),非 tradfi,不能纳入;
            # 也顺带修掉旧代码把 SPX6900(base=SPX)/PAXG/XAUT 等纯币误判的问题。
            is_tradfi = (
                utype in ("EQUITY", "KR_EQUITY", "PREMARKET", "COMMODITY")
                or "TRADFI" in subs
            )
            if not is_tradfi:
                continue
            if utype == "COMMODITY":
                sector = "Commodities"
            else:
                b = normalize_base(strip_stock_suffix(base_asset))
                sector = "Indices" if b in INDEX_BASES else "Stocks"  # QQQ/SPY 等指数 ETF 归 Indices
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


# ═══════════════════════════ Hyperliquid（HIP-3 TradFi 直连）═══════════════════════════


# crypto HIP-3（hyna 等）：整所都是币，不进 TradFi。混合 dex（cash 含 BTC+TSLA）靠 is_crypto_base 逐个排除。
HIP3_SKIP_DEXES = {"hyna"}


class Hyperliquid:
    """HIP-3 builder 市场 TradFi 永续：POST api.hyperliquid.xyz/info。

    符号格式 xyz:NVDA（instrument_id 含 dex 前缀）；crypto 主 dex / crypto HIP-3（hyna）走 Coinglass。
    download_workers=2：HL info API 保守并发，配合 batch.slice / watermark 护栏。
    """

    name = "Hyperliquid"
    info = "https://api.hyperliquid.xyz/info"
    download_workers = 2
    _iv = {"1h": "1h", "4h": "4h", "1d": "1d"}
    _iv_ms = {"1h": 3_600_000, "4h": 14_400_000, "1d": DAY_MS}
    _CANDLE_LIMIT = 500
    _MAX_PAGES = 12

    def _post(self, http: Http, payload: dict) -> Any:
        res = http.post_json(self.info, payload)
        return res.data if res.ok else None

    def _list_dexes(self, http: Http) -> list[str]:
        raw = self._post(http, {"type": "perpDexs"})
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for item in raw:
            name = ""
            if isinstance(item, str) and item:
                name = item
            elif isinstance(item, dict):
                name = str(item.get("name") or "")
            if not name:
                continue
            if name.lower() in HIP3_SKIP_DEXES:
                continue
            out.append(name)
        return out

    def discover(self, http: Http) -> list[Instrument]:
        out: list[Instrument] = []
        seen: set[str] = set()
        for dex in self._list_dexes(http):
            data = self._post(http, {"type": "metaAndAssetCtxs", "dex": dex})
            if not isinstance(data, list) or len(data) < 2:
                continue
            meta = data[0]
            if not isinstance(meta, dict):
                continue
            universe = [u for u in (meta.get("universe") or []) if isinstance(u, dict)]
            # 未知 dex 若一半以上是已知加密 base，整所跳过（hyna 这类新盘不必等黑名单）。
            bases_in_dex: list[str] = []
            for u in universe:
                coin = str(u.get("name") or "")
                if ":" not in coin:
                    continue
                bases_in_dex.append(normalize_base(strip_stock_suffix(coin.split(":", 1)[1])))
            if bases_in_dex:
                crypto_n = sum(1 for b in bases_in_dex if is_crypto_base(b))
                if crypto_n / len(bases_in_dex) >= 0.5:
                    continue
            for u in universe:
                coin = str(u.get("name") or "")
                if ":" not in coin or coin in seen:
                    continue
                base_raw = coin.split(":", 1)[1]
                sector = classify_sector(base_raw, native_stock=True)
                if sector is None:
                    continue
                base = normalize_base(strip_stock_suffix(base_raw))
                seen.add(coin)
                out.append(Instrument(self.name, coin, base, sector, "perp", "USD"))
        return out

    def _dex_for(self, inst: Instrument) -> str:
        return inst.instrument_id.split(":", 1)[0]

    def fetch_price(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        iv = self._iv[interval]
        iv_ms = self._iv_ms[interval]
        rows: dict[int, dict] = {}
        end = int(time.time() * 1000)
        start_floor = end - DEFAULT_MAX_DAILY_ROWS * iv_ms
        for _ in range(self._MAX_PAGES):
            start = max(start_floor, end - self._CANDLE_LIMIT * iv_ms)
            data = self._post(http, {
                "type": "candleSnapshot",
                "req": {
                    "coin": inst.instrument_id,
                    "interval": iv,
                    "startTime": start,
                    "endTime": end,
                },
            })
            if not isinstance(data, list) or not data:
                break
            earliest = None
            for c in data:
                if not isinstance(c, dict):
                    continue
                vol = to_num(c.get("v"))
                close = to_num(c.get("c"))
                vol_usd = (vol * close) if vol is not None and close is not None else None
                row = _mk_price_row(
                    c.get("t"), c.get("o"), c.get("h"), c.get("l"), c.get("c"),
                    vol_usd, estimated=True,
                )
                if row:
                    rows[row["time"]] = row
                t = to_ms(c.get("t"))
                if t is not None:
                    earliest = t if earliest is None else min(earliest, t)
            if earliest is None or len(data) < self._CANDLE_LIMIT:
                break
            end = earliest - 1
            if end <= start_floor:
                break
        return [rows[t] for t in sorted(rows)]

    def fetch_oi(self, http: Http, inst: Instrument, interval: str) -> list[dict]:
        data = self._post(http, {"type": "metaAndAssetCtxs", "dex": self._dex_for(inst)})
        if not isinstance(data, list) or len(data) < 2:
            return []
        meta, ctxs = data[0], data[1]
        if not isinstance(meta, dict) or not isinstance(ctxs, list):
            return []
        for i, u in enumerate(meta.get("universe") or []):
            if str(u.get("name") or "") != inst.instrument_id:
                continue
            if i >= len(ctxs) or not isinstance(ctxs[i], dict):
                return []
            ctx = ctxs[i]
            oi_base = to_num(ctx.get("openInterest"))
            mark = to_num(ctx.get("markPx"))
            if oi_base is None or mark is None:
                return []
            return [{"time": _now_day_ms(), "oi_usd": oi_base * mark}]
        return []

    def fetch_funding(self, http: Http, inst: Instrument) -> list[dict]:
        rows: dict[int, dict] = {}
        end = int(time.time() * 1000)
        start_floor = end - DEFAULT_MAX_DAILY_ROWS * DAY_MS
        window = 180 * DAY_MS
        start = max(start_floor, end - window)
        for _ in range(30):
            data = self._post(http, {
                "type": "fundingHistory",
                "coin": inst.instrument_id,
                "startTime": start,
                "endTime": end,
            })
            if not isinstance(data, list) or not data:
                break
            earliest = None
            for d in data:
                if not isinstance(d, dict):
                    continue
                tm = to_ms(d.get("time"))
                rate = to_num(d.get("fundingRate"))
                if tm is not None and rate is not None:
                    rows[tm] = {"time": tm, "rate": rate}
                    earliest = tm if earliest is None else min(earliest, tm)
            if earliest is None or len(data) < 500:
                break
            end = earliest - 1
            start = max(start_floor, end - window)
            if end <= start_floor:
                break
        return [rows[t] for t in sorted(rows)]


# 是否有"历史 OI"可回补;True=按 interval 拉真历史,False(默认)=download.py 每日存 1 个快照点。
# CryptoCom 曾用 get-valuations?valuation_type=open_interest,该接口已失效(HTTP 400),
# 现改走 get-tickers 的 oi 快照字段,故降级为快照型(不再声明 oi_history)。
Gate.oi_history = True
Bybit.oi_history = True
Binance.oi_history = True

ALL_ADAPTERS: list = [
    OKX(), Gate(), Bitget(), MEXC(), HTX(), CryptoCom(), Coinbase(), Kraken(), Bybit(), Binance(),
    Hyperliquid(),
]


def get_adapters(names: list[str] | None = None) -> list:
    if not names:
        return ALL_ADAPTERS
    want = {n.lower() for n in names}
    return [a for a in ALL_ADAPTERS if a.name.lower() in want]
