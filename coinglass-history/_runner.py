"""Shared CLI runners for coinglass-history module download.py scripts."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

import _common as C

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _watermark as WM  # noqa: E402

EXCHANGE_LIST_PARAM = ",".join(C.TRACKED_EXCHANGES)

OPTION_SYMBOLS = ("BTC", "ETH")
OPTION_EXCHANGES = ("Deribit", "Binance", "OKX")
OPTION_UNITS = ("USD",)
OPTION_OI_RANGES = ("1h", "4h", "12h")


def _parse_csv_filters(args: Any) -> tuple[set[str] | None, set[str] | None, set[str] | None]:
    ex_f = set(e.strip() for e in args.exchange.split(",")) if getattr(args, "exchange", None) else None
    sym_f = set(s.strip() for s in args.symbol.split(",")) if getattr(args, "symbol", None) else None
    iv_f = set(i.strip() for i in args.interval.split(",")) if getattr(args, "interval", None) else None
    return ex_f, sym_f, iv_f


def _add_download_args(p: argparse.ArgumentParser, *, with_exchange: bool = False) -> None:
    if with_exchange:
        p.add_argument("--exchange", default=None, help="Comma-separated exchanges")
    p.add_argument("--symbol", default=None)
    p.add_argument("--interval", default=None)
    p.add_argument("--force", action="store_true")
    p.add_argument("--recheck-days", type=int, default=C.DEFAULT_RECHECK_DAYS)
    p.add_argument("--retry", type=int, default=3)
    p.add_argument("--sleep", type=float, default=0.25)
    p.add_argument("--max-failures", type=int, default=50)
    p.add_argument("--max-tuples", type=int, default=0)
    p.add_argument(
        "--workers",
        type=int,
        default=C.DEFAULT_WORKERS,
        help=f"Concurrent download workers (default {C.DEFAULT_WORKERS})",
    )
    p.add_argument("--api-key", default=None)


def run_per_instrument(
    endpoint: str,
    market: str,
    module_file: str,
    *,
    extra_params: dict[str, str] | None = None,
) -> int:
    here = C.module_dir(module_file)
    cache_dir = here / "cache"
    status_csv = here / "output" / "_run_status.csv"

    p = argparse.ArgumentParser(
        description=f"Download {endpoint} per (exchange, instrument, interval)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_download_args(p, with_exchange=True)
    args = p.parse_args()
    api_key = C.resolve_api_key(args.api_key)
    ex_f, sym_f, iv_f = _parse_csv_filters(args)
    intervals = list(C.SUPPORTED_INTERVALS) if not args.interval else [i.strip() for i in args.interval.split(",")]

    by_ex = C.load_instruments(market)
    tuples: list[tuple[str, str, str]] = []
    excluded_tradfi = 0
    for exchange, items in sorted(by_ex.items()):
        if ex_f and exchange not in ex_f:
            continue
        for it in items:
            iid = it.get("instrument_id") or ""
            # coinglass 停 tradfi：Stocks/Commodities/Indices 改由交易所直连独占，此处排除
            base = it.get("base_asset") or C.extract_base_from_instrument(iid)
            if C.is_tradfi_base(base):
                excluded_tradfi += 1
                continue
            if sym_f and iid not in sym_f:
                if base not in sym_f and not any(iid.startswith(s) for s in sym_f):
                    continue
            for interval in intervals:
                tuples.append((exchange, iid, interval))
    if args.max_tuples and args.max_tuples > 0:
        tuples = tuples[: args.max_tuples]
    if excluded_tradfi:
        print(f"[tradfi-excluded] skipped {excluded_tradfi} coinglass tradfi instruments (Stocks/Commodities/Indices)", flush=True)

    cache_dir.mkdir(parents=True, exist_ok=True)
    status_csv.parent.mkdir(parents=True, exist_ok=True)
    workers = max(1, int(getattr(args, "workers", C.DEFAULT_WORKERS) or C.DEFAULT_WORKERS))
    print(
        f"endpoint={endpoint}  market={market}  tuples={len(tuples)}  workers={workers}  cache={cache_dir}",
        flush=True,
    )

    limiter = C.ApiRequestLimiter(args.sleep)
    failures = C.FailureBudget(args.max_failures)
    stats = C.JobStats()
    wm_gate = WM.Gate()  # 磁盘/outbox 高水位门闩:触发后剩余 tuple 直接跳过,JSON 不再涨盘

    def _worker(idx: int, job: tuple[str, str, str]) -> dict:
        exchange, symbol, interval = job
        if failures.exceeded() or wm_gate.blocked():
            return {
                "index": idx,
                "exchange": exchange,
                "symbol": symbol,
                "interval": interval,
                "status_code": 0,
                "ok": False,
                "skipped": True,
                "error_body": "watermark blocked" if wm_gate.blocked() else "failure budget exceeded",
            }

        cf = cache_dir / f"{C.safe_filename(exchange, symbol, interval)}.csv"
        cached = {} if args.force else C.read_ohlc_cache(cf)
        emax = max(cached.keys()) if cached else None
        latest_date = C.ts_to_date(emax) if emax else ""

        if C.is_series_fresh(emax, interval, force=args.force):
            stats.inc("fresh")
            return {
                "index": idx,
                "exchange": exchange,
                "symbol": symbol,
                "interval": interval,
                "status_code": 0,
                "ok": True,
                "rows_added": 0,
                "cache_total": len(cached),
                "latest_date": latest_date,
                "skipped_fresh": True,
            }

        since_ms = max(0, emax - args.recheck_days * C.DAY_S * 1000) if emax is not None else None
        limit = min(C.INTERVAL_MAX_ROWS.get(interval, C.COINGLASS_MAX_LIMIT), C.COINGLASS_MAX_LIMIT)
        params = {
            "exchange": exchange,
            "symbol": symbol,
            "interval": interval,
            "limit": str(limit),
        }
        if extra_params:
            params.update(extra_params)
        if since_ms is not None:
            params["start_time"] = str(since_ms)

        limiter.wait()
        try:
            status, payload = C.http_get_json(
                C.API_BASE + endpoint, params=params, api_key=api_key, retries=args.retry
            )
        except Exception as e:
            status, payload = -1, str(e)

        row: dict[str, Any] = {
            "index": idx,
            "exchange": exchange,
            "symbol": symbol,
            "interval": interval,
            "status_code": status,
        }

        if status != 200 or not isinstance(payload, dict):
            stats.inc("fail")
            err = payload if isinstance(payload, str) else str(payload)[:300]
            row.update({"ok": False, "cache_total": len(cached), "error_body": err[:400]})
            if failures.record():
                print(f"\nSTOPPED: failures exceeded limit={args.max_failures}", flush=True)
            return row

        if str(payload.get("code")) != "0":
            stats.inc("fail")
            row.update(
                {
                    "ok": False,
                    "cache_total": len(cached),
                    "error_body": f"code={payload.get('code')} msg={payload.get('msg')!r}"[:400],
                }
            )
            if failures.record():
                print(f"\nSTOPPED: failures exceeded limit={args.max_failures}", flush=True)
            return row

        rows = C.extract_ohlc_rows(payload.get("data") or [])
        if not rows and not cached:
            stats.inc("empty")
            row.update({"ok": True, "rows_added": 0, "cache_total": 0, "latest_date": ""})
            return row

        merged, added = C.merge_ohlc_rows(cached, rows, args.recheck_days, emax)
        C.write_ohlc_cache(cf, merged)
        latest_ms = max(merged.keys()) if merged else None
        latest_date = C.ts_to_date(latest_ms) if latest_ms else ""
        row.update(
            {
                "ok": True,
                "rows_added": added,
                "cache_total": len(merged),
                "latest_date": latest_date,
            }
        )
        if added == 0 and emax is not None:
            stats.inc("fresh")
        else:
            stats.inc("new")
        return row

    jobs = [(idx, tup) for idx, tup in enumerate(tuples, 1)]

    def _progress(rows: list[dict]) -> None:
        C.write_status_csv(status_csv, rows)
        last = rows[-1] if rows else {}
        print(
            f"[progress] {len(rows)}/{len(tuples)} "
            f"last={last.get('exchange', '')}_{last.get('symbol', '')} {last.get('interval', '')} "
            f"fetched={stats.new} skipped_fresh={stats.fresh} empty={stats.empty} failed={stats.fail}",
            flush=True,
        )

    C.run_indexed_jobs(
        jobs,
        _worker,
        workers=workers,
        status_csv=status_csv,
        progress_hook=_progress,
        progress_every=50,
    )

    print(
        f"\nDONE. fetched={stats.new} skipped_fresh={stats.fresh} "
        f"empty={stats.empty} failed={stats.fail}",
        flush=True,
    )
    return 2 if stats.fail else 0


def run_per_symbol_main(
    endpoint: str,
    module_file: str,
    *,
    market: str = "futures",
    extra_params: dict[str, str] | None = None,
    skip_symbols: frozenset[str] | None = None,
    symbol_scope: str = "top200",
) -> int:
    here = C.module_dir(module_file)
    p = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter)
    C.add_per_symbol_args(p)
    args = p.parse_args()
    api_key = C.resolve_api_key(args.api_key)
    return C.run_per_symbol_download(
        endpoint,
        market,
        here / "cache",
        here / "output" / "_run_status.csv",
        args,
        api_key,
        extra_params=extra_params,
        skip_symbols=skip_symbols,
        symbol_scope=symbol_scope,
    )


def _merge_chart_wide(
    cached: dict[tuple[int, str], float],
    chart: dict[str, Any],
) -> dict[tuple[int, str], float]:
    out = dict(cached)
    time_list = chart.get("time_list") or []
    price_list = chart.get("price_list") or []
    data_map = chart.get("data_map") or {}
    if not isinstance(time_list, list):
        return out
    for i, ts_raw in enumerate(time_list):
        ts = C.normalize_ts_ms(ts_raw)
        if ts is None:
            continue
        if isinstance(price_list, list) and i < len(price_list) and price_list[i] not in (None, ""):
            try:
                out[(ts, "_price")] = float(price_list[i])
            except (ValueError, TypeError):
                pass
        if isinstance(data_map, dict):
            for ex, series in data_map.items():
                if not isinstance(series, list) or i >= len(series) or series[i] in (None, ""):
                    continue
                try:
                    out[(ts, str(ex))] = float(series[i])
                except (ValueError, TypeError):
                    pass
    return out


def run_option_exchange_chart(
    endpoint: str,
    module_file: str,
    *,
    ranges: tuple[str, ...] = OPTION_OI_RANGES,
    with_range: bool = True,
) -> int:
    here = C.module_dir(module_file)
    cache_dir = here / "cache"
    status_csv = here / "output" / "_run_status.csv"

    p = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbol", default=None)
    p.add_argument("--unit", default=None)
    p.add_argument("--range", dest="range_", default=None)
    p.add_argument("--force", action="store_true")
    p.add_argument("--retry", type=int, default=3)
    p.add_argument("--sleep", type=float, default=0.5)
    p.add_argument(
        "--workers",
        type=int,
        default=C.DEFAULT_WORKERS,
        help=f"Concurrent download workers (default {C.DEFAULT_WORKERS})",
    )
    p.add_argument("--api-key", default=None)
    args = p.parse_args()
    api_key = C.resolve_api_key(args.api_key)

    sym_list = list(OPTION_SYMBOLS) if not args.symbol else [s.strip() for s in args.symbol.split(",")]
    units = list(OPTION_UNITS) if not args.unit else [u.strip() for u in args.unit.split(",")]

    if with_range:
        rngs = list(ranges) if not args.range_ else [r.strip() for r in args.range_.split(",")]
        tuples = [(s, u, r) for s in sym_list for u in units for r in rngs]
        name_fn: Callable[..., str] = lambda s, u, r: C.safe_filename(s, u, r)
    else:
        tuples = [(s, u, "") for s in sym_list for u in units]
        name_fn = lambda s, u, _: C.safe_filename(s, u)

    cache_dir.mkdir(parents=True, exist_ok=True)
    status_csv.parent.mkdir(parents=True, exist_ok=True)
    workers = max(1, int(getattr(args, "workers", C.DEFAULT_WORKERS) or C.DEFAULT_WORKERS))
    print(f"endpoint={endpoint}  tuples={len(tuples)}  workers={workers}  cache={cache_dir}", flush=True)

    limiter = C.ApiRequestLimiter(args.sleep)
    failures = C.FailureBudget(50)
    stats = C.JobStats()
    status_rows: list[dict] = []

    def _worker(idx: int, tup: tuple[str, str, str]) -> dict:
        symbol, unit, rng = tup
        if failures.exceeded():
            return {"index": idx, "ok": False, "skipped": True}

        cf = cache_dir / f"{name_fn(symbol, unit, rng)}.csv"
        cached = {} if args.force else C.read_wide_cache(cf)
        freshness_key = rng or "all"
        emax = C.latest_wide_ts_ms(cached)

        if C.is_series_fresh(emax, freshness_key, force=args.force):
            stats.inc("fresh")
            return {
                "index": idx,
                "exchange": "",
                "symbol": f"{symbol}/{unit}" + (f"/{rng}" if rng else ""),
                "interval": rng or "all",
                "status_code": 0,
                "ok": True,
                "rows_added": 0,
                "cache_total": len(cached),
                "skipped_fresh": True,
            }

        params: dict[str, str] = {"symbol": symbol, "unit": unit}
        if with_range and rng:
            params["range"] = rng

        limiter.wait()
        try:
            status, payload = C.http_get_json(
                C.API_BASE + endpoint, params=params, api_key=api_key, retries=args.retry
            )
        except Exception as e:
            status, payload = -1, str(e)

        row = {
            "index": idx,
            "exchange": "",
            "symbol": f"{symbol}/{unit}" + (f"/{rng}" if rng else ""),
            "interval": rng or "all",
            "status_code": status,
        }
        if status != 200 or not isinstance(payload, dict) or str(payload.get("code")) != "0":
            stats.inc("fail")
            row.update({"ok": False, "cache_total": len(cached), "error_body": str(payload)[:300]})
            failures.record()
            return row

        data = payload.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            data = data[0]
        merged = _merge_chart_wide(cached, data if isinstance(data, dict) else {})
        if merged:
            C.write_wide_cache(cf, merged)
        row.update({"ok": True, "rows_added": len(merged) - len(cached), "cache_total": len(merged)})
        stats.inc("new")
        return row

    jobs = [(idx, tup) for idx, tup in enumerate(tuples, 1)]

    def _progress(rows: list[dict]) -> None:
        C.write_status_csv(status_csv, rows)

    status_rows = C.run_indexed_jobs(
        jobs,
        _worker,
        workers=workers,
        progress_hook=_progress,
        progress_every=1,
    )

    print(
        f"\nDONE. fetched={stats.new} skipped_fresh={stats.fresh} failed={stats.fail}",
        flush=True,
    )
    return 2 if stats.fail else 0


def run_option_max_pain(module_file: str) -> int:
    here = C.module_dir(module_file)
    cache_dir = here / "cache"
    status_csv = here / "output" / "_run_status.csv"
    endpoint = "/api/option/max-pain"

    p = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbol", default=None)
    p.add_argument("--exchange", default=None)
    p.add_argument("--retry", type=int, default=3)
    p.add_argument("--sleep", type=float, default=0.5)
    p.add_argument("--api-key", default=None)
    args = p.parse_args()
    api_key = C.resolve_api_key(args.api_key)

    symbols = list(OPTION_SYMBOLS) if not args.symbol else [s.strip() for s in args.symbol.split(",")]
    exchanges = list(OPTION_EXCHANGES) if not args.exchange else [e.strip() for e in args.exchange.split(",")]
    cache_dir.mkdir(parents=True, exist_ok=True)
    status_csv.parent.mkdir(parents=True, exist_ok=True)
    failures = 0
    fetch_ms = C.now_ms()

    legacy_json_dir = here / "output" / "json"

    for symbol, exchange in [(s, e) for s in symbols for e in exchanges]:
        cache_path = cache_dir / f"{C.safe_filename(symbol, exchange)}.json"
        existing: dict[str, Any] = {}
        if cache_path.is_file():
            try:
                existing = json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}
        elif legacy_json_dir.is_dir():
            legacy_path = legacy_json_dir / f"{C.safe_filename(symbol, exchange)}.json"
            if legacy_path.is_file():
                try:
                    legacy_doc = json.loads(legacy_path.read_text(encoding="utf-8"))
                    if isinstance(legacy_doc, dict):
                        existing = {
                            "symbol": symbol,
                            "exchange": exchange,
                            "snapshots": legacy_doc.get("snapshots") or [],
                        }
                except (OSError, json.JSONDecodeError):
                    existing = {}
        snapshots = existing.get("snapshots") if isinstance(existing.get("snapshots"), list) else []

        status, payload = C.http_get_json(
            C.API_BASE + endpoint,
            params={"symbol": symbol, "exchange": exchange},
            api_key=api_key,
            retries=args.retry,
        )
        if status != 200 or not isinstance(payload, dict) or str(payload.get("code")) != "0":
            failures += 1
            time.sleep(args.sleep)
            continue

        for item in payload.get("data") or []:
            if not isinstance(item, dict):
                continue
            snap = dict(item)
            snap["fetch_time_ms"] = fetch_ms
            expiry = snap.get("date") or snap.get("expiry")
            if expiry is not None:
                snap["expiry"] = str(expiry)
            snapshots.append(snap)

        cache_path.write_text(
            json.dumps(
                {"symbol": symbol, "exchange": exchange, "snapshots": snapshots},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        time.sleep(args.sleep)

    C.write_status_csv(
        status_csv,
        [{"ok": failures == 0, "symbol": "option-max-pain", "cache_total": len(list(cache_dir.glob("*.json")))}],
    )
    return 2 if failures else 0


def run_etf_list(module_file: str) -> int:
    here = C.module_dir(module_file)
    cache_dir = here / "cache"
    p = argparse.ArgumentParser()
    p.add_argument("--market", default=None)
    p.add_argument("--api-key", default=None)
    args = p.parse_args()
    api_key = C.resolve_api_key(args.api_key)
    markets = list(C.ETF_LIST_MARKETS) if not args.market else [m.strip() for m in args.market.split(",")]
    cache_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for market in markets:
        status, payload = C.fetch_etf_endpoint(market, "list", api_key=api_key)
        if status != 200 or not isinstance(payload, dict) or str(payload.get("code")) != "0":
            failures += 1
            err = payload if isinstance(payload, str) else f"status={status} code={payload.get('code') if isinstance(payload, dict) else '?'}"
            print(f"  etf-list {market}: FAIL ({err[:120]})", flush=True)
            continue
        data = payload.get("data") or []
        n = len(data) if isinstance(data, list) else 0
        (cache_dir / f"{market}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  etf-list {market}: OK ({n} tickers)", flush=True)
    return 2 if failures else 0


def run_etf_aum(module_file: str) -> int:
    here = C.module_dir(module_file)
    cache_dir = here / "cache"
    p = argparse.ArgumentParser()
    p.add_argument("--market", default="bitcoin")
    p.add_argument("--force", action="store_true")
    p.add_argument("--recheck-days", type=int, default=C.ETF_RECHECK_DAYS)
    p.add_argument("--api-key", default=None)
    args = p.parse_args()
    api_key = C.resolve_api_key(args.api_key)
    markets = [m.strip() for m in args.market.split(",")]
    cache_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    skipped = 0
    for market in markets:
        cf = cache_dir / f"{market}.csv"
        cached = {} if args.force else C.read_ohlc_cache(cf)
        emax = C.latest_ohlc_ts_ms(cached)
        latest_date = C.ts_to_date(emax) if emax else ""

        if C.should_skip_api_fetch(emax, "1d", force=args.force, recheck_days=args.recheck_days):
            skipped += 1
            print(
                f"  etf-aum {market}: skipped fresh  (cached {len(cached)}, latest {latest_date})",
                flush=True,
            )
            continue

        status, payload = C.fetch_etf_endpoint(market, "aum", api_key=api_key)
        if status != 200 or not isinstance(payload, dict) or str(payload.get("code")) != "0":
            failures += 1
            continue
        rows: list[dict[str, Any]] = []
        for item in payload.get("data") or []:
            if not isinstance(item, dict):
                continue
            ts = C.normalize_ts_ms(item.get("time") or item.get("timestamp"))
            if ts is None:
                continue
            extra: dict[str, Any] = {}
            v = item.get("aum_usd")
            if v not in (None, ""):
                try:
                    extra["aum_usd"] = float(v)
                except (ValueError, TypeError):
                    pass
            rows.append(
                {
                    "time_ms": ts,
                    "open": "",
                    "high": "",
                    "low": "",
                    "close": "",
                    "volume_usd": "",
                    "extra_json": json.dumps(extra, ensure_ascii=False, separators=(",", ":")) if extra else "",
                }
            )
        emax = C.latest_ohlc_ts_ms(cached)
        merged, added = C.merge_ohlc_rows(cached, rows, 2, emax)
        if merged:
            C.write_ohlc_cache(cf, merged)
        latest_date = C.ts_to_date(C.latest_ohlc_ts_ms(merged)) if merged else ""
        print(
            f"  etf-aum {market}: +{added} new  (cached {len(merged)}, latest {latest_date})",
            flush=True,
        )
    if skipped:
        print(f"  etf-aum skipped_fresh={skipped}", flush=True)
    return 2 if failures else 0


def _ensure_json_merge_path() -> None:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def csv_to_json_per_instrument(module_file: str) -> None:
    _ensure_json_merge_path()
    import _json_merge as JM  # noqa: E402

    here = C.module_dir(module_file)
    cache_dir = here / "cache"
    json_dir = here / "output" / "json"
    if not cache_dir.exists():
        raise SystemExit(f"cache dir not found: {cache_dir}")
    json_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for p in sorted(cache_dir.glob("*.csv")):
        stem = p.stem
        parts = stem.split("_")
        if len(parts) < 3:
            continue
        exchange, symbol = parts[0], "_".join(parts[1:-1])
        interval = parts[-1]
        rows = C.read_ohlc_cache(p)
        if not rows:
            continue
        candles = _rows_to_candles(rows)
        payload = {
            "code": "0",
            "msg": "success",
            "exchange": exchange,
            "symbol": symbol,
            "interval": interval,
            "data": candles,
        }
        out = json_dir / f"{stem}.json"
        JM.write_coinglass_candles_json(out, payload)
        written += 1
    print(f"DONE. wrote={written} -> {json_dir}")


def csv_to_json_per_symbol(module_file: str) -> None:
    here = C.module_dir(module_file)
    p = argparse.ArgumentParser()
    C.add_per_symbol_args(p)
    args = p.parse_args()
    C.per_symbol_csv_to_json(here / "cache", here / "output" / "json", args)


def csv_to_json_wide_chart(
    module_file: str,
    *,
    symbol_key: str = "symbol",
    interval_key: str = "interval",
    stem_parts: int = 2,
) -> None:
    _ensure_json_merge_path()
    import _json_merge as JM  # noqa: E402

    here = C.module_dir(module_file)
    cache_dir = here / "cache"
    json_dir = here / "output" / "json"
    json_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for p in sorted(cache_dir.glob("*.csv")):
        stem = p.stem
        parts = stem.split("_")
        if len(parts) < stem_parts:
            continue
        if stem_parts == 2:
            symbol, unit = parts[0], parts[1]
            interval = ""
        else:
            symbol, unit, interval = parts[0], parts[1], parts[2]
        wide = C.read_wide_cache(p)
        if not wide:
            continue
        times = sorted({t for (t, _) in wide.keys()})
        time_list: list[int] = []
        price_list: list[float | None] = []
        data_map: dict[str, list[float | None]] = {}
        keys = sorted({k for (_, k) in wide.keys() if k != "_price"})
        for ex in keys:
            data_map[ex] = []
        for ts in times:
            time_list.append(ts)
            price_list.append(wide.get((ts, "_price")))
            for ex in keys:
                data_map[ex].append(wide.get((ts, ex)))
        payload: dict[str, Any] = {
            "code": "0",
            "msg": "success",
            symbol_key: symbol,
            "data": {
                "time_list": time_list,
                "price_list": price_list,
                "data_map": data_map,
            },
        }
        if unit:
            payload["unit"] = unit
        if interval_key:
            payload[interval_key] = interval
        out = json_dir / f"{stem}.json"
        JM.write_dashboard_json(out, payload, merge_data_fn=JM.merge_wide_chart_data)
        written += 1
    print(f"DONE. wrote={written} -> {json_dir}")


def csv_to_json_etf_market(module_file: str) -> None:
    _ensure_json_merge_path()
    import _json_merge as JM  # noqa: E402

    here = C.module_dir(module_file)
    cache_dir = here / "cache"
    json_dir = here / "output" / "json"
    json_dir.mkdir(parents=True, exist_ok=True)
    for cf in sorted(cache_dir.glob("*.csv")):
        market = cf.stem
        rows = C.read_ohlc_cache(cf)
        if not rows:
            continue
        candles = _rows_to_candles(rows)
        payload = {"code": "0", "msg": "success", "market": market, "data": candles}
        JM.write_coinglass_candles_json(json_dir / f"{market}.json", payload)


def csv_to_json_etf_list(module_file: str) -> None:
    here = C.module_dir(module_file)
    cache_dir = here / "cache"
    json_dir = here / "output" / "json"
    p = argparse.ArgumentParser()
    p.add_argument("--market", default=None)
    args = p.parse_args()
    market_filter = (
        {m.strip() for m in args.market.split(",") if m.strip()} if args.market else None
    )
    if not cache_dir.is_dir():
        raise SystemExit(f"cache dir not found: {cache_dir}")
    json_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for path in sorted(cache_dir.glob("*.json")):
        market = path.stem
        if market_filter is not None and market not in market_filter:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  skip {path.name}: {exc}", flush=True)
            continue
        if not isinstance(data, list):
            print(f"  skip {path.name}: expected JSON array", flush=True)
            continue
        doc = {"code": "0", "msg": "success", "market": market, "data": data}
        (json_dir / f"{market}.json").write_text(
            json.dumps(doc, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        written += 1
    print(f"DONE. wrote={written} -> {json_dir}")


def csv_to_json_option_max_pain(module_file: str) -> None:
    here = C.module_dir(module_file)
    cache_dir = here / "cache"
    json_dir = here / "output" / "json"
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default=None)
    p.add_argument("--exchange", default=None)
    args = p.parse_args()
    symbol_filter = (
        {s.strip() for s in args.symbol.split(",") if s.strip()} if args.symbol else None
    )
    exchange_filter = (
        {e.strip() for e in args.exchange.split(",") if e.strip()} if args.exchange else None
    )
    if not cache_dir.is_dir():
        raise SystemExit(f"cache dir not found: {cache_dir}")
    json_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for path in sorted(cache_dir.glob("*.json")):
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  skip {path.name}: {exc}", flush=True)
            continue
        if not isinstance(cached, dict):
            continue
        symbol = str(cached.get("symbol") or path.stem.split("_", 1)[0])
        exchange = str(cached.get("exchange") or "")
        if not exchange and "_" in path.stem:
            exchange = path.stem.split("_", 1)[1]
        if symbol_filter is not None and symbol not in symbol_filter:
            continue
        if exchange_filter is not None and exchange not in exchange_filter:
            continue
        snapshots = cached.get("snapshots")
        if not isinstance(snapshots, list):
            snapshots = []
        doc = {
            "code": "0",
            "msg": "success",
            "symbol": symbol,
            "exchange": exchange,
            "snapshots": snapshots,
        }
        out = json_dir / f"{C.safe_filename(symbol, exchange)}.json"
        out.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        written += 1
    print(f"DONE. wrote={written} -> {json_dir}")


def _rows_to_candles(rows: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    candles: list[dict[str, Any]] = []
    for ts in sorted(rows.keys()):
        r = rows[ts]
        c: dict[str, Any] = {"time": ts}
        for k in ("open", "high", "low", "close", "volume_usd"):
            v = r.get(k)
            if v not in (None, ""):
                try:
                    c[k] = float(v)
                except (ValueError, TypeError):
                    pass
        extra = r.get("extra_json") or ""
        if extra:
            try:
                eo = json.loads(extra)
                if isinstance(eo, dict):
                    for k, v in eo.items():
                        c.setdefault(k, v)
            except json.JSONDecodeError:
                pass
        candles.append(c)
    return candles
