"""审计探针:逐所 dump 发现端点里可能标记 tradfi 的字段分布 + 现适配器发现数。

只读,不写盘。用于核对"不错杀不遗漏"。
  python _audit_probe.py            # 全部
  python _audit_probe.py OKX Gate   # 指定
"""
from __future__ import annotations

import sys
from collections import Counter

from _common import Http
import exchanges as X


def _p(title, obj):
    print(f"  {title}: {obj}")


def probe_okx(http):
    r = http.get_json("https://www.okx.com/api/v5/public/instruments?instType=SWAP")
    data = r.data.get("data") or []
    print(f"[OKX] SWAP 合约总数 {len(data)}")
    _p("instCategory 分布", dict(Counter(str(x.get('instCategory')) for x in data)))
    # 看非 crypto category 的样例
    for cat in ("2", "3", "4", "5"):
        ex = [x.get("instId") for x in data if str(x.get("instCategory")) == cat][:8]
        if ex:
            _p(f"instCategory={cat} 样例", ex)


def probe_gate(http):
    r = http.get_json("https://api.gateio.ws/api/v4/futures/usdt/contracts")
    data = r.data if isinstance(r.data, list) else []
    print(f"[Gate] usdt 合约总数 {len(data)}")
    _p("一个样本的 keys", list(data[0].keys()) if data else [])
    # Gate 可能有 type / 分类字段
    for k in ("type",):
        _p(f"字段 {k} 分布", dict(Counter(str(x.get(k)) for x in data)))
    # 找疑似股票(纯 ticker,3-5 位字母,非常见币)
    known = {"BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA"}
    susp = [x.get("name") for x in data if x.get("name", "").split("_")[0] in
            ("TSLA", "AAPL", "NVDA", "MSTR", "COIN", "GOOGL", "AMZN", "META", "MSFT", "HOOD", "SPY", "QQQ")]
    _p("疑似股票 ticker 命中", susp[:20] or "无")


def probe_bitget(http):
    r = http.get_json("https://api.bitget.com/api/v2/mix/market/contracts?productType=USDT-FUTURES")
    data = r.data.get("data") or []
    print(f"[Bitget] 合约总数 {len(data)}")
    _p("一个样本的 keys", list(data[0].keys()) if data else [])
    for k in ("isRwa", "symbolType", "contractType"):
        vals = Counter(str(x.get(k)) for x in data)
        if len(vals) > 1 or (data and k in data[0]):
            _p(f"字段 {k} 分布", dict(vals))
    susp = [x.get("symbol") for x in data if (x.get("baseCoin") or "").upper() in
            ("TSLA", "AAPL", "NVDA", "MSTR", "COIN", "GOOGL", "SPY", "QQQ")]
    _p("疑似股票 ticker 命中", susp[:20] or "无")


def probe_mexc(http):
    r = http.get_json_multi([h + "/api/v1/contract/detail" for h in
                             ("https://futures.mexc.com", "https://contract.mexc.com")])
    data = r.data.get("data") or []
    print(f"[MEXC] 合约总数 {len(data)}")
    _p("一个样本的 keys", list(data[0].keys()) if data else [])
    # 找含 STOCK 后缀的
    stock = [x.get("symbol") for x in data if "STOCK" in (x.get("symbol") or "").upper()]
    _p("含 STOCK 命名数", len(stock))
    _p("STOCK 样例", stock[:8])


def probe_bybit(http):
    data = []
    cursor = ""
    for _ in range(10):
        url = "https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000"
        if cursor:
            url += f"&cursor={cursor}"
        r = http.get_json(url)
        res = r.data.get("result") or {}
        data += res.get("list") or []
        cursor = res.get("nextPageCursor") or ""
        if not cursor:
            break
    print(f"[Bybit] linear 合约总数 {len(data)}")
    _p("一个样本的 keys", list(data[0].keys()) if data else [])
    _p("symbolType 分布", dict(Counter(str(x.get('symbolType')) for x in data)))
    for st in ("stock", "commodity", "index", "forex"):
        ex = [x.get("symbol") for x in data if str(x.get("symbolType", "")).lower() == st][:8]
        if ex:
            _p(f"symbolType={st} 样例", ex)


def probe_htx(http):
    r = http.get_json_multi([h + "/linear-swap-api/v1/swap_contract_info" for h in
                             ("https://api.hbdm.vn", "https://api.hbdm.com")])
    data = r.data.get("data") or []
    print(f"[HTX] 合约总数 {len(data)}")
    _p("一个样本的 keys", list(data[0].keys()) if data else [])
    susp = [x.get("contract_code") for x in data if (x.get("contract_code") or "").split("-")[0] in
            ("TSLA", "AAPL", "NVDA", "MSTR", "COIN", "GOOGL", "SPY", "QQQ", "XAU", "XAG")]
    _p("疑似股票/大宗命中", susp[:20] or "无")


def probe_cryptocom(http):
    r = http.get_json("https://api.crypto.com/exchange/v1/public/get-instruments")
    data = (r.data.get("result") or {}).get("data") or []
    perps = [x for x in data if (x.get("symbol") or "").endswith("-PERP")]
    print(f"[Crypto.com] 合约总数 {len(data)}, PERP {len(perps)}")
    _p("一个 PERP 样本的 keys", list(perps[0].keys()) if perps else [])
    for k in ("inst_type", "instrument_type", "cnl", "category"):
        if perps and k in perps[0]:
            _p(f"字段 {k} 分布", dict(Counter(str(x.get(k)) for x in perps)))
    susp = [x.get("symbol") for x in perps if x.get("symbol", "").replace("-PERP", "").rstrip("USD") in
            ("TSLA", "AAPL", "NVDA", "MSTR", "COIN", "GOOGL", "SPY", "QQQ")]
    _p("疑似股票命中", susp[:20] or "无")


def probe_kraken(http):
    r = http.get_json("https://futures.kraken.com/derivatives/api/v3/instruments")
    data = r.data.get("instruments") or []
    perps = [x for x in data if str(x.get("symbol", "")).upper().startswith("PF_")]
    print(f"[Kraken] instruments {len(data)}, PF_ 永续 {len(perps)}")
    _p("一个 PF_ 样本的 keys", list(perps[0].keys()) if perps else [])
    for k in ("type", "category", "tradeable", "underlying", "assetClass"):
        if perps and k in perps[0]:
            _p(f"字段 {k} 分布(前12)", dict(list(Counter(str(x.get(k)) for x in perps).items())[:12]))


def probe_coinbase(http):
    r = http.get_json("https://api.international.coinbase.com/api/v1/instruments")
    data = r.data if isinstance(r.data, list) else []
    perps = [x for x in data if (x.get("type") or "") == "PERP"]
    print(f"[Coinbase] instruments {len(data)}, PERP {len(perps)}")
    _p("一个 PERP 样本的 keys", list(perps[0].keys()) if perps else [])
    for k in ("asset_class", "instrument_class", "base_asset_name", "category"):
        if perps and k in perps[0]:
            _p(f"字段 {k} 分布(前15)", dict(list(Counter(str(x.get(k)) for x in perps).items())[:15]))


PROBES = {
    "OKX": probe_okx, "Gate": probe_gate, "Bitget": probe_bitget, "MEXC": probe_mexc,
    "Bybit": probe_bybit, "HTX": probe_htx, "Crypto.com": probe_cryptocom,
    "Kraken": probe_kraken, "Coinbase": probe_coinbase,
}


def main():
    http = Http()
    want = sys.argv[1:] or list(PROBES)
    for name in want:
        fn = PROBES.get(name)
        if not fn:
            continue
        try:
            fn(http)
        except Exception as e:  # noqa: BLE001
            print(f"[{name}] 探测失败: {e}")
        # 现适配器发现数
        try:
            adp = {a.name: a for a in X.get_adapters([name])}
            a = adp.get(name)
            if a:
                insts = a.discover(http)
                by = Counter(i.sector for i in insts)
                print(f"  >>> 现适配器发现: {len(insts)}  {dict(by)}")
        except Exception as e:  # noqa: BLE001
            print(f"  >>> 现适配器发现失败: {e}")
        print()


if __name__ == "__main__":
    main()
