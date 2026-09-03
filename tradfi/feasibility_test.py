"""TradFi 可行性轻量测试。

对 10 家交易所做最小验证：可达性 + 单标的取一页数据，输出可用性矩阵。
无第三方依赖；支持 HTTPS_PROXY / HTTP_PROXY / ALL_PROXY 环境变量。

用法:
  python feasibility_test.py
  $env:HTTPS_PROXY="http://127.0.0.1:7890"; python feasibility_test.py   # 走代理再测对比
"""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request

UA = "tradfi-feasibility/1.0"
TIMEOUT = 20
_CTX = ssl.create_default_context()


def _opener():
    handlers = [urllib.request.HTTPSHandler(context=_CTX)]
    proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
             or os.environ.get("ALL_PROXY") or os.environ.get("all_proxy"))
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


_OPENER = _opener()


def get(url: str):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with _OPENER.open(req, timeout=TIMEOUT) as r:
            return True, json.loads(r.read().decode("utf-8")), ""
    except urllib.error.HTTPError as e:
        return False, None, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return False, None, str(e)[:60]


def _n(x):
    return len(x) if isinstance(x, (list, dict)) else (0 if x is None else 1)


# 每家: (发现URL, 取发现条数函数, 单标的K线URL, 取K线行数函数)
def probe_okx():
    ok, d, err = get("https://www.okx.com/api/v5/public/instruments?instType=SWAP")
    disc = len([r for r in (d.get("data") if ok else []) if str(r.get("instCategory") or "") in ("3", "4")]) if ok else 0
    ok2, d2, err2 = get("https://www.okx.com/api/v5/market/history-candles?instId=XAU-USDT-SWAP&bar=1D&limit=5")
    rows = _n(d2.get("data")) if ok2 else 0
    return ok, disc, rows, err or err2


def probe_gate():
    ok, d, err = get("https://api.gateio.ws/api/v4/futures/usdt/contracts?limit=1000")
    disc = _n(d) if ok else 0
    ok2, d2, err2 = get("https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract=XAU_USDT&interval=1d&limit=5")
    rows = _n(d2) if ok2 else 0
    return ok, disc, rows, err or err2


def probe_bitget():
    ok, d, err = get("https://api.bitget.com/api/v2/mix/market/contracts?productType=USDT-FUTURES")
    disc = _n(d.get("data")) if ok else 0
    ok2, d2, err2 = get("https://api.bitget.com/api/v2/mix/market/candles?symbol=XAUUSDT&productType=USDT-FUTURES&granularity=1D&limit=5")
    rows = _n(d2.get("data")) if ok2 else 0
    return ok, disc, rows, err or err2


def probe_mexc():
    for host in ("https://futures.mexc.com", "https://contract.mexc.com"):
        ok, d, err = get(f"{host}/api/v1/contract/detail")
        if ok:
            disc = _n(d.get("data"))
            ok2, d2, _ = get(f"{host}/api/v1/contract/kline/XAU_USDT?interval=Day1")
            rows = _n((d2.get("data") or {}).get("time")) if ok2 else 0
            return True, disc, rows, f"via {host}"
    return False, 0, 0, err


def probe_htx():
    for host in ("https://api.hbdm.vn", "https://api.hbdm.com"):
        ok, d, err = get(f"{host}/linear-swap-api/v1/swap_contract_info")
        if ok:
            disc = _n(d.get("data"))
            ok2, d2, _ = get(f"{host}/linear-swap-ex/market/history/kline?contract_code=XAU-USDT&period=1day&size=5")
            rows = _n(d2.get("data")) if ok2 else 0
            return True, disc, rows, f"via {host}"
    return False, 0, 0, err


def probe_cryptocom():
    ok, d, err = get("https://api.crypto.com/exchange/v1/public/get-instruments")
    disc = _n((d.get("result") or {}).get("data")) if ok else 0
    ok2, d2, err2 = get("https://api.crypto.com/exchange/v1/public/get-candlestick?instrument_name=XAUUSD-PERP&timeframe=1D&count=5")
    rows = _n((d2.get("result") or {}).get("data")) if ok2 else 0
    return ok, disc, rows, err or err2


def probe_coinbase():
    ok, d, err = get("https://api.international.coinbase.com/api/v1/instruments")
    disc = len([r for r in d if (r.get("type") == "PERP")]) if ok and isinstance(d, list) else 0
    return ok, disc, -1, (err or "candles 需校准分页")


def probe_kraken():
    ok, d, err = get("https://futures.kraken.com/derivatives/api/v3/instruments")
    disc = len([r for r in (d.get("instruments") if ok else []) if str(r.get("symbol") or "").upper().startswith("PF_")]) if ok else 0
    ok2, d2, err2 = get("https://futures.kraken.com/api/charts/v1/trade/PF_XAUTUSD/1d")
    rows = _n(d2.get("candles")) if ok2 else 0
    return ok, disc, rows, err or err2


def probe_binance():
    ok, d, err = get("https://fapi.binance.com/fapi/v1/exchangeInfo")
    disc = _n(d.get("symbols")) if ok else 0
    ok2, d2, err2 = get("https://fapi.binance.com/fapi/v1/klines?symbol=XAUUSDT&interval=1d&limit=5")
    rows = _n(d2) if ok2 else 0
    return ok, disc, rows, err or err2


def probe_bybit():
    ok, d, err = get("https://api.bybit.com/v5/market/instruments-info?category=linear&limit=100")
    disc = _n((d.get("result") or {}).get("list")) if ok else 0
    ok2, d2, err2 = get("https://api.bybit.com/v5/market/kline?category=linear&symbol=XAUUSDT&interval=D&limit=5")
    rows = _n((d2.get("result") or {}).get("list")) if ok2 else 0
    return ok, disc, rows, err or err2


PROBES = [
    ("OKX", probe_okx), ("Gate", probe_gate), ("Bitget", probe_bitget),
    ("MEXC", probe_mexc), ("HTX", probe_htx), ("Crypto.com", probe_cryptocom),
    ("Coinbase", probe_coinbase), ("Kraken", probe_kraken),
    ("Binance", probe_binance), ("Bybit", probe_bybit),
]


def main() -> None:
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("ALL_PROXY") or "(无)"
    print(f"代理: {proxy}\n")
    print(f"{'交易所':<12}{'可达':<6}{'发现数':<8}{'样本行':<8}备注")
    print("-" * 60)
    result = {"proxy": proxy, "at": int(time.time()), "exchanges": {}}
    for name, fn in PROBES:
        t0 = time.time()
        try:
            ok, disc, rows, note = fn()
        except Exception as e:  # noqa: BLE001
            ok, disc, rows, note = False, 0, 0, str(e)[:60]
        flag = "OK" if ok else "封/错"
        rows_s = "-" if rows == -1 else str(rows)
        print(f"{name:<12}{flag:<6}{disc:<8}{rows_s:<8}{note}  ({time.time()-t0:.1f}s)")
        result["exchanges"][name] = {"reachable": ok, "discovered": disc,
                                     "sample_rows": rows, "note": note}
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feasibility_result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n已写 {out}")


if __name__ == "__main__":
    main()
