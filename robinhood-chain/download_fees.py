#!/usr/bin/env python3
"""Download Robinhood Chain daily fees / revenue from DefiLlama (no Dune).

Outputs:
  output/json/rh_fees_daily.json

口径（DefiLlama methodology）：
  Fees    = 用户在 Robinhood Chain 支付的 gas（含 L2 执行与 L1 data fee）
  Revenue = Fees 扣除 Ethereum L1/blob 成本与 Arbitrum Expansion 10% 分成后的净额

与 Token Terminal「收入」同量级，但不是同一张原表；写稿须注明 DefiLlama。

Usage:
  python download_fees.py
  python download_fees.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
JSON_DIR = HERE / "output" / "json"

LLAMA_BASE = "https://api.llama.fi/summary/fees/robinhood-chain"
LLAMA_SERIES = (("fees_usd", "dailyFees"), ("revenue_usd", "dailyRevenue"))
LLAMA_ATTEMPTS = 4
LLAMA_TIMEOUT = 90
SOURCE_URL = "https://defillama.com/chain/Robinhood%20Chain"


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get_json(url: str) -> dict:
    last: Exception | None = None
    for attempt in range(1, LLAMA_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "dashboard-data/robinhood-chain-fees"}
            )
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
        "name": "rh_fees_daily",
        "description": "Robinhood Chain 日手续费与净收入(DefiLlama)",
        "source_url": SOURCE_URL,
        "source": "defillama",
        "methodology": {
            "Fees": "Transaction gas fees paid by users on Robinhood Chain (ETH), covering both L2 execution and the L1 data fee component.",
            "Revenue": "Transaction gas fees net of Ethereum L1 execution and blob costs and the 10% Arbitrum Expansion Program fee share.",
        },
        "updated_at": now_utc_iso(),
        "row_count": len(rows),
        "columns": ["day", "fees_usd", "revenue_usd"],
        "data": rows,
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", default=str(JSON_DIR), help="输出目录")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写盘")
    args = parser.parse_args()

    print("[robinhood-fees] DefiLlama robinhood-chain fees/revenue")
    if args.dry_run:
        print("DRY-RUN: 将请求 dailyFees + dailyRevenue，不写盘")
        return

    started = time.time()
    payload = build_fees_payload()
    dest = Path(args.out) / "rh_fees_daily.json"
    write_json(dest, payload)
    fees_sum = sum(float(r.get("fees_usd") or 0) for r in payload["data"])
    rev_sum = sum(float(r.get("revenue_usd") or 0) for r in payload["data"])
    print(
        f"    rows={payload['row_count']} fees_cum={fees_sum:,.0f} "
        f"revenue_cum={rev_sum:,.0f} {time.time() - started:.1f}s"
    )
    print(f"json -> {dest}")


if __name__ == "__main__":
    main()
