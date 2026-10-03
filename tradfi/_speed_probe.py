"""本机 TradFi API 拉取速度探测（供 VPS 对比）。

对每家测：发现接口 RTT + 字节、日线 K 线较大页、吞吐估算。
无第三方依赖；支持 HTTPS_PROXY。

用法:
  python _speed_probe.py
  python _speed_probe.py --rounds 3
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import statistics
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

UA = "tradfi-speed-probe/1.0"
TIMEOUT = 45
_CTX = ssl.create_default_context()


def _opener():
    handlers = [urllib.request.HTTPSHandler(context=_CTX)]
    proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("ALL_PROXY")
        or os.environ.get("all_proxy")
    )
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


_OPENER = _opener()


def fetch(url: str) -> dict:
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with _OPENER.open(req, timeout=TIMEOUT) as r:
            raw = r.read()
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return {
                "ok": True,
                "status": getattr(r, "status", 200),
                "bytes": len(raw),
                "ms": round(elapsed_ms, 1),
                "kb_s": round((len(raw) / 1024) / (elapsed_ms / 1000), 2) if elapsed_ms > 0 else 0,
                "err": "",
            }
    except urllib.error.HTTPError as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        body = b""
        try:
            body = e.read() or b""
        except Exception:  # noqa: BLE001
            pass
        return {
            "ok": False,
            "status": e.code,
            "bytes": len(body),
            "ms": round(elapsed_ms, 1),
            "kb_s": 0,
            "err": f"HTTP {e.code}",
        }
    except Exception as e:  # noqa: BLE001
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {
            "ok": False,
            "status": 0,
            "bytes": 0,
            "ms": round(elapsed_ms, 1),
            "kb_s": 0,
            "err": str(e)[:80],
        }


def avg_fetch(url: str, rounds: int) -> dict:
    samples = [fetch(url) for _ in range(rounds)]
    ok_samples = [s for s in samples if s["ok"]]
    base = samples[-1]
    if not ok_samples:
        return {**base, "rounds": rounds, "ok_rounds": 0, "ms_avg": base["ms"], "ms_p50": base["ms"]}
    ms_list = [s["ms"] for s in ok_samples]
    bytes_list = [s["bytes"] for s in ok_samples]
    kb_list = [s["kb_s"] for s in ok_samples]
    return {
        "ok": True,
        "status": ok_samples[-1]["status"],
        "bytes": int(statistics.mean(bytes_list)),
        "ms": round(statistics.mean(ms_list), 1),
        "ms_avg": round(statistics.mean(ms_list), 1),
        "ms_p50": round(statistics.median(ms_list), 1),
        "ms_min": round(min(ms_list), 1),
        "ms_max": round(max(ms_list), 1),
        "kb_s": round(statistics.mean(kb_list), 2),
        "rounds": rounds,
        "ok_rounds": len(ok_samples),
        "err": "",
    }


# (交易所, 发现URL, 较大K线/历史URL, 备注)
CASES = [
    (
        "OKX",
        "https://www.okx.com/api/v5/public/instruments?instType=SWAP",
        "https://www.okx.com/api/v5/market/history-candles?instId=XAU-USDT-SWAP&bar=1D&limit=300",
        "VPN 通常需要",
    ),
    (
        "Gate",
        "https://api.gateio.ws/api/v4/futures/usdt/contracts?limit=1000",
        "https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract=XAU_USDT&interval=1d&limit=1000",
        "直连常可用",
    ),
    (
        "Bitget",
        "https://api.bitget.com/api/v2/mix/market/contracts?productType=USDT-FUTURES",
        "https://api.bitget.com/api/v2/mix/market/candles?symbol=XAUUSDT&productType=USDT-FUTURES&granularity=1D&limit=200",
        "VPN 通常需要",
    ),
    (
        "MEXC",
        "https://futures.mexc.com/api/v1/contract/detail",
        "https://futures.mexc.com/api/v1/contract/kline/XAU_USDT?interval=Day1",
        "备用域 futures.mexc.com",
    ),
    (
        "HTX",
        "https://api.hbdm.vn/linear-swap-api/v1/swap_contract_info",
        "https://api.hbdm.vn/linear-swap-ex/market/history/kline?contract_code=XAU-USDT&period=1day&size=2000",
        "备用域 api.hbdm.vn",
    ),
    (
        "Crypto.com",
        "https://api.crypto.com/exchange/v1/public/get-instruments",
        "https://api.crypto.com/exchange/v1/public/get-candlestick?instrument_name=XAUUSD-PERP&timeframe=1D&count=300",
        "本机不稳定",
    ),
    (
        "Coinbase",
        "https://api.international.coinbase.com/api/v1/instruments",
        "https://api.international.coinbase.com/api/v1/instruments/BTC-PERP/candles?granularity=ONE_DAY",
        "INTX；K线参数可能需校准",
    ),
    (
        "Kraken",
        "https://futures.kraken.com/derivatives/api/v3/instruments",
        "https://futures.kraken.com/api/charts/v1/trade/PF_XAUTUSD/1d",
        "大响应",
    ),
    (
        "Binance-fapi",
        "https://fapi.binance.com/fapi/v1/exchangeInfo",
        "https://fapi.binance.com/fapi/v1/klines?symbol=XAUUSDT&interval=1d&limit=500",
        "本机常 451",
    ),
    (
        "Binance-spot",
        "https://api.binance.com/api/v3/exchangeInfo",
        "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=1000",
        "bStocks/现货通道",
    ),
    (
        "Binance-vision",
        "https://data-api.binance.vision/api/v3/ticker/24hr?symbol=BTCUSDT",
        "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1d/BTCUSDT-1d-2024-01-01.zip",
        "批量历史 zip",
    ),
    (
        "Bybit",
        "https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000",
        "https://api.bybit.com/v5/market/kline?category=linear&symbol=XAUUSDT&interval=D&limit=1000",
        "本机常 403",
    ),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3, help="每端点重复次数取平均")
    args = ap.parse_args()
    rounds = max(1, args.rounds)

    proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("ALL_PROXY")
        or os.environ.get("all_proxy")
        or "(无)"
    )
    at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    print(f"测速时间: {at}")
    print(f"代理: {proxy}")
    print(f"每端点轮次: {rounds}\n")
    hdr = f"{'交易所':<16}{'发现ms':>8}{'发现KB':>8}{'发现KB/s':>10}{'K线ms':>8}{'K线KB':>8}{'K线KB/s':>10}  备注"
    print(hdr)
    print("-" * len(hdr))

    rows = []
    wall0 = time.perf_counter()
    for name, discover_url, kline_url, note in CASES:
        d = avg_fetch(discover_url, rounds)
        k = avg_fetch(kline_url, rounds)
        d_ms = f"{d['ms_avg']:.0f}" if d["ok"] else d["err"][:10]
        k_ms = f"{k['ms_avg']:.0f}" if k["ok"] else k["err"][:10]
        d_kb = f"{d['bytes']/1024:.1f}" if d["ok"] else "-"
        k_kb = f"{k['bytes']/1024:.1f}" if k["ok"] else "-"
        d_rate = f"{d['kb_s']:.1f}" if d["ok"] else "-"
        k_rate = f"{k['kb_s']:.1f}" if k["ok"] else "-"
        status_note = note
        if not d["ok"] and d["err"]:
            status_note = f"{note}; 发现失败:{d['err']}"
        elif not k["ok"] and k["err"]:
            status_note = f"{note}; K线失败:{k['err']}"
        print(
            f"{name:<16}{d_ms:>8}{d_kb:>8}{d_rate:>10}{k_ms:>8}{k_kb:>8}{k_rate:>10}  {status_note}"
        )
        rows.append(
            {
                "exchange": name,
                "note": note,
                "discover": d,
                "kline": k,
                "discover_url": discover_url,
                "kline_url": kline_url,
            }
        )

    wall_s = round(time.perf_counter() - wall0, 1)
    out = {
        "at_local": at,
        "proxy": proxy,
        "rounds": rounds,
        "wall_seconds": wall_s,
        "machine_hint": "国内本机（与方案文档同一测速场景）",
        "metric_说明": {
            "ms_avg": "成功轮次平均耗时(毫秒)，含 DNS+TLS+首字节+收完 body",
            "bytes": "响应体字节数",
            "kb_s": "有效吞吐 = bytes/1024 / (ms/1000)，受延迟与包体大小共同影响",
            "对比建议": "VPS 上用同一脚本、同一 URL、同一 --rounds 复测后填对照表",
        },
        "exchanges": rows,
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "speed_probe_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n总墙钟: {wall_s}s")
    print(f"已写 {out_path}")


if __name__ == "__main__":
    main()
