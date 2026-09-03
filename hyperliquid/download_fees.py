"""Download Hyperliquid fee / volume-split / HyperEVM DEX series into local JSON.

Three outputs, deliberately kept as three files so each carries one source:

  output/json/hl_fees_daily.json    DefiLlama  实测手续费与 revenue(日度)
  output/json/hl_volume_split.json  Dune 8426019  成交量拆分 Core/HIP-3 + HyperEVM 链上费用
  output/json/hyperevm_dex.json     Dune 8426052  HyperEVM 各 DEX 成交量/笔数/用户

为什么费用不取 Dune 8426019 的 trading_fees_proxy_usd:
  该列精确等于 total_trading_volume_usd * 3.7bps —— 1192 个完整交易日的比值在 1e-9
  精度内只有一个取值,是按固定费率推算的,不是实测。与 DefiLlama 实测值逐日对比,近
  14 天高估 1.07x-1.77x(中位数 1.53x),因为真实费率随做市商占比在 2.10-3.99bps 之间
  浮动:高成交日反而更低。所以本脚本在写盘时就丢掉 trading_fees_proxy_usd 与
  total_fees_usd 两列,避免它们被下游误当成费用收入。hyperevm_fees_usd 是链上实测,保留。

为什么走 _dune.py 而不是 hyperliquid/download.py 的 dune-client:
  这两个 query 是公开查询但不属于本账号。dune-client 的 get_latest_result 只读缓存,
  owner 一停调度就会静默冻结(uni-burn 曾因此连发六天同一份数据)。_dune.fetch_query
  在缓存超过 --max-age-hours 时会自己触发一次执行。

Usage:
  python download_fees.py                 # 缓存超过 20h 才触发执行
  python download_fees.py --dry-run       # 只看两个 Dune query 的缓存新鲜度
  python download_fees.py --no-execute    # 只读缓存,不花执行额度
  python download_fees.py --only hl_fees_daily
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT_DIR = HERE.parent
JSON_DIR = HERE / "output" / "json"

sys.path.insert(0, str(ROOT_DIR))
from _dune import (  # noqa: E402
    DuneApi,
    add_common_args,
    build_payload,
    fetch_query,
    now_utc_iso,
    print_dry_run,
    resolve_api_key,
    write_json,
)

VOLUME_SPLIT_QUERY = 8426019
HYPEREVM_DEX_QUERY = 8426052

DUNE_QUERIES: dict[str, tuple[int, str]] = {
    "hl_volume_split": (
        VOLUME_SPLIT_QUERY,
        "Hyperliquid 成交量拆分(Core / HIP-3)及 HyperEVM 链上费用",
    ),
    "hyperevm_dex": (
        HYPEREVM_DEX_QUERY,
        "HyperEVM 各 DEX 日成交量、交易笔数与去重交易地址",
    ),
}

LLAMA_BASE = "https://api.llama.fi/summary/fees/hyperliquid"
LLAMA_SERIES = (("fees_usd", "dailyFees"), ("revenue_usd", "dailyRevenue"))
LLAMA_ATTEMPTS = 4
LLAMA_TIMEOUT = 90

# 8426019 把最后一行标成滚动 24h 快照(口径与前面的完整日不同),不能混进日序列。
COMPLETED_DAY_WINDOW = "completed_day"
# 只发布这些列,其余(费用推算值)在写盘时丢弃。
VOLUME_SPLIT_COLUMNS = (
    "day",
    "core_daily_volume_usd",
    "hip3_daily_volume_usd",
    "total_trading_volume_usd",
    "hyperevm_fees_usd",
)


def day_key(value: object) -> str:
    """Dune 的 day 可能是 '2026-08-23' 或 '2026-08-23 00:00:00.000 UTC'。"""
    return str(value or "").strip()[:10]


def http_get_json(url: str) -> dict:
    last: Exception | None = None
    for attempt in range(1, LLAMA_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "dashboard-data/hyperliquid-fees"})
            with urllib.request.urlopen(req, timeout=LLAMA_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < LLAMA_ATTEMPTS:
                wait = min(2**attempt, 15)
                print(f"    [warn] attempt {attempt}/{LLAMA_ATTEMPTS}: {exc}; retry in {wait}s")
                time.sleep(wait)
    raise RuntimeError(f"GET {url} failed after {LLAMA_ATTEMPTS} attempts: {last}")


def fetch_llama_series(data_type: str) -> dict[str, float]:
    payload = http_get_json(f"{LLAMA_BASE}?dataType={data_type}")
    chart = payload.get("totalDataChart") or []
    out: dict[str, float] = {}
    for point in chart:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        ts, value = point[0], point[1]
        if not isinstance(ts, (int, float)) or not isinstance(value, (int, float)):
            continue
        day = datetime.fromtimestamp(int(ts), timezone.utc).date().isoformat()
        out[day] = float(value)
    return out


def build_fees_payload() -> dict:
    """DefiLlama 实测费用 + revenue 合成一条日序列。"""
    series: dict[str, dict[str, float]] = {}
    for column, data_type in LLAMA_SERIES:
        print(f"    GET {data_type}")
        for day, value in fetch_llama_series(data_type).items():
            series.setdefault(day, {})[column] = value

    rows = [{"day": day, **series[day]} for day in sorted(series)]
    return {
        "code": "0",
        "msg": "success",
        "query_id": None,
        "name": "hl_fees_daily",
        "description": "Hyperliquid 实测日手续费与 revenue(DefiLlama)",
        "source_url": "https://defillama.com/protocol/hyperliquid",
        "source": "defillama",
        "updated_at": now_utc_iso(),
        "row_count": len(rows),
        "columns": ["day", "fees_usd", "revenue_usd"],
        "data": rows,
    }


def build_volume_split_payload(api: DuneApi, args: argparse.Namespace) -> dict:
    query_id, desc = DUNE_QUERIES["hl_volume_split"]
    result = fetch_query(api, query_id, args)
    payload = build_payload("hl_volume_split", query_id, desc, result)

    completed: list[dict] = []
    rolling: dict | None = None
    for row in result.rows:
        trimmed = {key: row.get(key) for key in VOLUME_SPLIT_COLUMNS}
        trimmed["day"] = day_key(row.get("day"))
        if row.get("volume_window") == COMPLETED_DAY_WINDOW:
            completed.append(trimmed)
        else:
            # 最新一行是官方 API 的滚动 24h 快照,单独放,别让它当成完整日。
            rolling = {**trimmed, "volume_window": row.get("volume_window"), "row_source": row.get("row_source")}

    completed.sort(key=lambda r: r["day"])
    payload["data"] = completed
    payload["columns"] = list(VOLUME_SPLIT_COLUMNS)
    payload["row_count"] = len(completed)
    payload["rolling_24h"] = rolling
    payload["dropped_columns"] = ["trading_fees_proxy_usd", "total_fees_usd"]
    payload["dropped_reason"] = "按成交量 * 固定 3.7bps 推算,非实测;费用改用 hl_fees_daily(DefiLlama)"
    return payload


def build_hyperevm_dex_payload(api: DuneApi, args: argparse.Namespace) -> dict:
    query_id, desc = DUNE_QUERIES["hyperevm_dex"]
    result = fetch_query(api, query_id, args)
    payload = build_payload("hyperevm_dex", query_id, desc, result)

    rows = [{**row, "day": day_key(row.get("day"))} for row in result.rows]
    rows.sort(key=lambda r: (r["day"], str(r.get("project") or "")))
    payload["data"] = rows
    payload["columns"] = ["day", "project", "trades", "unique_traders", "volume_usd"]
    payload["row_count"] = len(rows)
    payload["traders_note"] = "unique_traders 为各 DEX 各自去重,跨 DEX 相加会重复计算同一地址"
    return payload


BUILDERS = {
    "hl_fees_daily": lambda api, args: build_fees_payload(),
    "hl_volume_split": build_volume_split_payload,
    "hyperevm_dex": build_hyperevm_dex_payload,
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--only", default=None, choices=sorted(BUILDERS.keys()), help="只跑一个输出")
    parser.add_argument("--out", default=str(JSON_DIR), help="输出目录(默认 output/json)")
    add_common_args(parser, default_max_age_hours=20.0)
    args = parser.parse_args()

    api = DuneApi(resolve_api_key(args.api_key, ROOT_DIR))
    out_dir = Path(args.out)

    if args.dry_run:
        targets = (
            {args.only: DUNE_QUERIES[args.only]}
            if args.only in DUNE_QUERIES
            else {k: v for k, v in DUNE_QUERIES.items() if args.only is None}
        )
        if targets:
            print_dry_run(api, targets, args.max_age_hours, "hyperliquid-fees")
        else:
            print("[hyperliquid-fees] DRY-RUN: hl_fees_daily 走 DefiLlama,无 Dune 缓存判断")
        return

    names = [args.only] if args.only else list(BUILDERS.keys())
    ok = 0
    failed: list[str] = []
    print(f"[hyperliquid-fees] 生成 {len(names)} 个输出 → {out_dir}")
    for i, name in enumerate(names, 1):
        started = time.time()
        try:
            print(f"  [{i}/{len(names)}] {name}")
            payload = BUILDERS[name](api, args)
            write_json(out_dir / f"{name}.json", payload)
            print(f"    rows={payload['row_count']:<6} {time.time() - started:.1f}s")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"    FAILED: {exc}")
            failed.append(name)
        time.sleep(max(0.0, args.sleep))

    print(f"\nDONE. ok={ok}/{len(names)} failed={len(failed)}" + (f" ({', '.join(failed)})" if failed else ""))
    print(f"json -> {out_dir}")
    if failed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
