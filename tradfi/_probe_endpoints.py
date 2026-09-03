"""一次性端点验证：对每家交易所拉一个 TradFi 永续，检查 discover/kline/OI/funding 响应格式。

仅用于开发期确认接口，跑通后可删除。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

UA = "tradfi-probe/1.0"


def get(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"_error": str(e)[:160]}


def head(x, n=2):
    if isinstance(x, list):
        return x[:n]
    if isinstance(x, dict):
        return {k: x[k] for k in list(x)[:6]}
    return x


def probe():
    out: dict = {}

    # ── OKX (XAU-USDT-SWAP) ──
    ok = {}
    ok["kline"] = head(get("https://www.okx.com/api/v5/market/candles?instId=XAU-USDT-SWAP&bar=1D&limit=3").get("data"))
    ok["oi"] = head(get("https://www.okx.com/api/v5/public/open-interest?instId=XAU-USDT-SWAP").get("data"))
    ok["funding"] = head(get("https://www.okx.com/api/v5/public/funding-rate-history?instId=XAU-USDT-SWAP&limit=3").get("data"))
    out["OKX"] = ok

    # ── Bybit (XAUUSDT linear) ──
    by = {}
    by["kline"] = head(get("https://api.bybit.com/v5/market/kline?category=linear&symbol=XAUUSDT&interval=D&limit=3").get("result", {}).get("list"))
    by["oi"] = head(get("https://api.bybit.com/v5/market/open-interest?category=linear&symbol=XAUUSDT&intervalTime=1d&limit=3").get("result", {}).get("list"))
    by["funding"] = head(get("https://api.bybit.com/v5/market/funding/history?category=linear&symbol=XAUUSDT&limit=3").get("result", {}).get("list"))
    out["Bybit"] = by

    # ── Bitget (XAUUSDT USDT-FUTURES) ──
    bg = {}
    bg["kline"] = head(get("https://api.bitget.com/api/v2/mix/market/candles?symbol=XAUUSDT&productType=USDT-FUTURES&granularity=1D&limit=3").get("data"))
    bg["oi"] = get("https://api.bitget.com/api/v2/mix/market/open-interest?symbol=XAUUSDT&productType=USDT-FUTURES").get("data")
    bg["funding"] = head(get("https://api.bitget.com/api/v2/mix/market/history-fund-rate?symbol=XAUUSDT&productType=USDT-FUTURES&pageSize=3").get("data"))
    out["Bitget"] = bg

    # ── Gate (XAU_USDT futures usdt) ──
    ga = {}
    ga["kline"] = head(get("https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract=XAU_USDT&interval=1d&limit=3"))
    ga["contract_stats"] = head(get("https://api.gateio.ws/api/v4/futures/usdt/contract_stats?contract=XAU_USDT&interval=1d&limit=3"))
    ga["funding"] = head(get("https://api.gateio.ws/api/v4/futures/usdt/funding_rate?contract=XAU_USDT&limit=3"))
    out["Gate"] = ga

    # ── MEXC (XAU_USDT contract) ──
    me = {}
    me["kline"] = head(get("https://contract.mexc.com/api/v1/contract/kline/XAU_USDT?interval=Day1"), 1)
    me["oi"] = get("https://contract.mexc.com/api/v1/contract/openInterest/XAU_USDT")
    me["funding"] = get("https://contract.mexc.com/api/v1/contract/funding_rate/XAU_USDT")
    out["MEXC"] = me

    # ── HTX (XAU-USDT linear swap) ──
    ht = {}
    ht["kline"] = head(get("https://api.hbdm.com/linear-swap-ex/market/history/kline?contract_code=XAU-USDT&period=1day&size=3").get("data"))
    ht["oi"] = head(get("https://api.hbdm.com/linear-swap-api/v1/swap_open_interest?contract_code=XAU-USDT").get("data"))
    ht["funding"] = get("https://api.hbdm.com/linear-swap-api/v1/swap_funding_rate?contract_code=XAU-USDT").get("data")
    out["HTX"] = ht

    # ── Crypto.com (XAUUSD-PERP) ──
    cr = {}
    cr["kline"] = head(get("https://api.crypto.com/exchange/v1/public/get-candlestick?instrument_name=XAUUSD-PERP&timeframe=1D&count=3").get("result", {}).get("data"))
    cr["valuation_oi"] = head(get("https://api.crypto.com/exchange/v1/public/get-valuations?instrument_name=XAUUSD-PERP&valuation_type=open_interest&count=3").get("result", {}).get("data"))
    cr["valuation_funding"] = head(get("https://api.crypto.com/exchange/v1/public/get-valuations?instrument_name=XAUUSD-PERP&valuation_type=funding_hist&count=3").get("result", {}).get("data"))
    out["Crypto.com"] = cr

    # ── Coinbase INTX (SPX-PERP as sample; XAU maybe GOLD-PERP) ──
    cb = {}
    cb["instruments_sample"] = head(get("https://api.international.coinbase.com/api/v1/instruments"), 1)
    cb["candles"] = head(get("https://api.international.coinbase.com/api/v1/instruments/SPX-PERP/candles?granularity=ONE_DAY&start=1735689600"), 1)
    out["Coinbase"] = cb

    # ── Kraken futures (PF_XAUTUSD) ──
    kr = {}
    kr["tickers_sample"] = head(get("https://futures.kraken.com/derivatives/api/v3/tickers").get("tickers"), 1)
    kr["ohlc"] = head(get("https://futures.kraken.com/api/charts/v1/trade/PF_XAUTUSD/1d?from=1735689600").get("candles"), 1)
    out["Kraken"] = kr

    # ── Binance (fapi XAUUSDT if exists) ──
    bn = {}
    bn["kline"] = head(get("https://fapi.binance.com/fapi/v1/klines?symbol=XAUUSDT&interval=1d&limit=3"), 1)
    bn["oi"] = head(get("https://fapi.binance.com/futures/data/openInterestHist?symbol=XAUUSDT&period=1d&limit=3"), 1)
    bn["funding"] = head(get("https://fapi.binance.com/fapi/v1/fundingRate?symbol=XAUUSDT&limit=3"), 1)
    out["Binance"] = bn

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    probe()
