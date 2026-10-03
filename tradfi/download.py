"""TradFi 原生 API 下载驱动。

用法：
  python download.py                      # 全部交易所，1d
  python download.py --exchanges OKX,Gate --intervals 1d,1h
  python download.py --discover-only      # 只发现标的、写 tradfi-symbols.json
  python download.py --no-oi --no-funding # 只拉价格

被封锁/不可达的交易所会记录错误并跳过，不影响其余。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

import _common as C
from _common import Http, Instrument
from exchanges import get_adapters

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _watermark as WM  # noqa: E402


def _finalize_oi(rows: list[dict], last_close: float | None) -> list[dict]:
    """把 oi_base 快照按最新收盘价换算成 oi_usd。"""
    out = []
    for r in rows:
        if "oi_usd" in r and r["oi_usd"] is not None:
            out.append({"time": r["time"], "oi_usd": r["oi_usd"]})
        elif "oi_base" in r and r["oi_base"] is not None and last_close:
            out.append({"time": r["time"], "oi_usd": r["oi_base"] * last_close})
    return out


def process_instrument(
    adapter, http: Http, inst: Instrument, intervals: list[str],
    do_oi: bool, do_funding: bool,
) -> dict:
    stat = {"exchange": inst.exchange, "instrument": inst.instrument_id,
            "price_rows": 0, "oi_rows": 0, "funding_rows": 0, "errors": []}
    last_close: float | None = None

    for iv in intervals:
        try:
            fresh = adapter.fetch_price(http, inst, iv)
        except Exception as e:  # noqa: BLE001
            stat["errors"].append(f"price/{iv}: {str(e)[:80]}")
            fresh = []
        if fresh:
            path = C.price_file(inst.exchange, inst.instrument_id, iv)
            merged = C.merge_rows(C.read_rows(path), fresh)
            C.write_rows(path, merged, meta={
                "exchange": inst.exchange, "symbol": inst.instrument_id,
                "base_asset": inst.base_asset, "sector": inst.sector,
                "interval": iv, "form": inst.form,
            })
            stat["price_rows"] += len(fresh)
            last_close = merged[-1].get("close") if merged else last_close

    if do_oi:
        # 历史型(Gate/CryptoCom/Bybit/Binance)按各 interval 拉真历史;
        # 快照型(其余 6 家)与 interval 无关,只存 1d 一份/天,逐日累积成序列。
        oi_intervals = intervals if getattr(adapter, "oi_history", False) else ["1d"]
        for iv in oi_intervals:
            try:
                oi = adapter.fetch_oi(http, inst, iv)
            except Exception as e:  # noqa: BLE001
                stat["errors"].append(f"oi/{iv}: {str(e)[:80]}")
                oi = []
            oi = _finalize_oi(oi, last_close)
            if oi:
                path = C.oi_file(inst.exchange, inst.instrument_id, iv)
                merged = C.merge_rows(C.read_rows(path), oi)
                C.write_rows(path, merged, meta={
                    "exchange": inst.exchange, "symbol": inst.instrument_id,
                    "base_asset": inst.base_asset, "sector": inst.sector, "interval": iv,
                })
                stat["oi_rows"] += len(oi)

    if do_funding:
        try:
            fund = adapter.fetch_funding(http, inst)
        except Exception as e:  # noqa: BLE001
            stat["errors"].append(f"funding: {str(e)[:80]}")
            fund = []
        if fund:
            path = C.funding_file(inst.exchange, inst.instrument_id)
            merged = C.merge_rows(C.read_rows(path), fund)
            C.write_rows(path, merged, meta={
                "exchange": inst.exchange, "symbol": inst.instrument_id,
                "base_asset": inst.base_asset, "sector": inst.sector,
            })
            stat["funding_rows"] += len(fund)

    return stat


def _merge_symbols(existing: list[dict], fresh: list[dict], exchange_names: set[str]) -> list[dict]:
    """--exchanges 部分运行时：只替换指定交易所条目，保留其余所。"""
    kept = [s for s in existing if str(s.get("exchange") or "") not in exchange_names]
    return kept + fresh


def main() -> None:
    ap = argparse.ArgumentParser(description="TradFi 原生 API 下载")
    ap.add_argument("--exchanges", default="", help="逗号分隔，默认全部")
    ap.add_argument("--intervals", default="1d", help="逗号分隔 1h,4h,1d，默认 1d")
    ap.add_argument("--discover-only", action="store_true")
    ap.add_argument("--no-oi", action="store_true")
    ap.add_argument("--no-funding", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit-per-exchange", type=int, default=0, help="每所最多处理 N 个标的（调试用）")
    args = ap.parse_args()

    C.ensure_dirs()
    stocks = C.load_tag_stocks()
    print(f"tag Stocks 集合: {len(stocks)} 个 base_asset（来自 {C.TAG_SUMMARY_PATH.name}）", flush=True)
    intervals = [x.strip() for x in args.intervals.split(",") if x.strip()]
    names = [x.strip() for x in args.exchanges.split(",") if x.strip()]
    adapters = get_adapters(names)
    http = Http()

    # ── 发现标的(第1轮:各所原生标记)──
    import exchanges as _ex
    MARKERLESS = {"HTX"}  # 无原生股票标记、靠跨所白名单兜底个股的交易所
    per_exchange: dict[str, list[Instrument]] = {}
    print("=== 发现 TradFi 标的 ===", flush=True)
    for a in adapters:
        try:
            insts = a.discover(http)
        except Exception as e:  # noqa: BLE001
            print(f"  {a.name:11} 发现失败: {str(e)[:80]}", flush=True)
            insts = []
        if args.limit_per_exchange:
            insts = insts[: args.limit_per_exchange]
        per_exchange[a.name] = insts

    # ── 第2轮:用跨所原生股票 ticker 白名单给 markerless 交易所(HTX)补个股 ──
    _ex.CROSS_STOCK_WHITELIST = {
        C.normalize_base(C.strip_stock_suffix(i.base_asset))
        for insts in per_exchange.values()
        for i in insts
        if i.sector == "Stocks" and not C.is_crypto_base(i.base_asset)
    }
    for a in adapters:
        if a.name in MARKERLESS:
            try:
                insts = a.discover(http)
            except Exception:  # noqa: BLE001
                insts = per_exchange.get(a.name, [])
            if args.limit_per_exchange:
                insts = insts[: args.limit_per_exchange]
            per_exchange[a.name] = insts

    # ── 汇总 + 打印 + 写 symbols ──
    all_symbols: list[dict] = []
    for a in adapters:
        insts = per_exchange.get(a.name, [])
        by_sector: dict[str, int] = {}
        for i in insts:
            by_sector[i.sector] = by_sector.get(i.sector, 0) + 1
            all_symbols.append(asdict(i))
        print(f"  {a.name:11} {len(insts):4d} 个  {by_sector}", flush=True)

    if names:
        existing: list[dict] = []
        if C.SYMBOLS_PATH.exists():
            try:
                doc = json.loads(C.SYMBOLS_PATH.read_text(encoding="utf-8"))
                existing = doc.get("symbols") or []
            except (OSError, json.JSONDecodeError):
                existing = []
        if existing:
            all_symbols = _merge_symbols(existing, all_symbols, {a.name for a in adapters})

    C.SYMBOLS_PATH.parent.mkdir(parents=True, exist_ok=True)
    C.SYMBOLS_PATH.write_text(
        json.dumps({"generated_at": int(time.time()), "count": len(all_symbols),
                    "symbols": all_symbols}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"已写 {C.SYMBOLS_PATH}  共 {len(all_symbols)} 个标的\n", flush=True)

    if args.discover_only:
        return

    # ── 拉取行情 ──
    print("=== 下载 price / OI / funding ===", flush=True)
    for a in adapters:
        insts = per_exchange.get(a.name) or []
        if not insts:
            continue
        wst = WM.check()  # 每所批次前查水位:磁盘将满 / outbox 堆积则中止本轮剩余
        if not wst.ok:
            print(f"  [watermark BLOCK] {wst.reason};中止本轮剩余交易所,已下载部分保留",
                  flush=True)
            break
        t0 = time.time()
        ex_workers = min(args.workers, getattr(a, "download_workers", args.workers))
        totals = {"price": 0, "oi": 0, "funding": 0, "ok": 0, "empty": 0, "err": 0}
        with ThreadPoolExecutor(max_workers=ex_workers) as ex:
            futs = [ex.submit(process_instrument, a, http, inst, intervals,
                              not args.no_oi, not args.no_funding) for inst in insts]
            for f in as_completed(futs):
                s = f.result()
                totals["price"] += s["price_rows"]
                totals["oi"] += s["oi_rows"]
                totals["funding"] += s["funding_rows"]
                if s["price_rows"] > 0:
                    totals["ok"] += 1
                else:
                    totals["empty"] += 1
                if s["errors"]:
                    totals["err"] += 1
        print(f"  {a.name:11} 标的 ok={totals['ok']} 空={totals['empty']} "
              f"price行={totals['price']} oi行={totals['oi']} funding行={totals['funding']} "
              f"({time.time()-t0:.1f}s)", flush=True)

    print("\n完成。输出目录: output/json/tradfi-price | tradfi-oi | tradfi-funding", flush=True)


if __name__ == "__main__":
    main()
