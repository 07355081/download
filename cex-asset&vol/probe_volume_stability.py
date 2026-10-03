# -*- coding: utf-8 -*-
"""CoinGecko volume_chart 定点历史稳定性探针（只读，不写 major_vol）。

每小时采样固定偏移日 D-7/30/90/180 的成交量，追加 jsonl；
用 --summary 汇总 max/min 漂移。策略切换不由此脚本决定。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from _paths import CACHE_DIR, ensure_cache_layout


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(Path(__file__).resolve().parent.parent / ".env")
API_KEY = (
    os.environ.get("COINGECKO_DEMO_API_KEY") or os.environ.get("COINGECKO_KEY") or ""
).strip()
API_BASE = "https://api.coingecko.com/api/v3/exchanges"
HEADERS = {"x-cg-demo-api-key": API_KEY}
PROBE_EXCHANGES = ("binance", "okex", "bybit_spot", "gdax")
PROBE_OFFSETS = (7, 30, 90, 180)
TIP_EXCLUDE_DAYS = 2
API_DAYS = 365
DRIFT_THRESHOLD = 0.001  # 0.1%

SAMPLES_PATH = CACHE_DIR / "probes" / "volume_chart_samples.jsonl"


def probe_days(now: datetime | None = None) -> list[str]:
    today = (now or datetime.now(timezone.utc)).date()
    days: list[str] = []
    for off in PROBE_OFFSETS:
        d = today - timedelta(days=off)
        if (today - d).days <= TIP_EXCLUDE_DAYS:
            continue
        days.append(d.isoformat())
    return days


def fetch_daily(exchange_id: str, days: int = API_DAYS) -> dict[str, float]:
    url = f"{API_BASE}/{exchange_id}/volume_chart"
    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            r = requests.get(url, headers=HEADERS, params={"days": str(days)}, timeout=60)
            r.raise_for_status()
            raw = r.json()
            break
        except requests.RequestException as e:
            last_err = e
            time.sleep(2**attempt)
    else:
        raise RuntimeError(f"{exchange_id}: {last_err}")

    daily: dict[str, float] = {}
    if not isinstance(raw, list):
        return daily
    for pt in raw:
        if not (isinstance(pt, list) and len(pt) >= 2):
            continue
        ts = int(pt[0]) / 1000
        day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        daily[day] = float(pt[1])
    return daily


def sample_once() -> int:
    ensure_cache_layout()
    SAMPLES_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    targets = probe_days()
    n = 0
    with SAMPLES_PATH.open("a", encoding="utf-8") as fh:
        for eid in PROBE_EXCHANGES:
            try:
                daily = fetch_daily(eid)
            except Exception as e:
                rec = {
                    "ts": ts,
                    "exchange": eid,
                    "day": None,
                    "value": None,
                    "error": str(e),
                }
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                print(f"[probe] FAIL {eid}: {e}", flush=True)
                continue
            for day in targets:
                val = daily.get(day)
                rec = {
                    "ts": ts,
                    "exchange": eid,
                    "day": day,
                    "value": val,
                    "error": None if val is not None else "missing",
                }
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
                status = f"{val:.6f}" if val is not None else "missing"
                print(f"[probe] {eid} {day} = {status}", flush=True)
            time.sleep(0.2)
    print(f"[probe] wrote {n} rows → {SAMPLES_PATH}", flush=True)
    return n


def load_samples() -> list[dict]:
    if not SAMPLES_PATH.is_file():
        return []
    rows: list[dict] = []
    with SAMPLES_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def summarize(threshold: float = DRIFT_THRESHOLD) -> int:
    rows = load_samples()
    if not rows:
        print(f"no samples at {SAMPLES_PATH}")
        return 1

    buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        if r.get("error") or r.get("value") is None or not r.get("day"):
            continue
        key = (str(r["exchange"]), str(r["day"]))
        buckets[key].append(float(r["value"]))

    print(
        f"{'exchange':<12} {'day':<12} {'n':>4} {'min':>16} {'max':>16} "
        f"{'max/min-1':>12} {'cv':>10} {'drift?':>8}"
    )
    drifted = 0
    for (ex, day) in sorted(buckets):
        vals = buckets[(ex, day)]
        vmin, vmax = min(vals), max(vals)
        ratio = (vmax / vmin - 1.0) if vmin > 0 else float("inf")
        mean = statistics.fmean(vals)
        cv = (statistics.pstdev(vals) / mean) if mean else 0.0
        flag = ratio > threshold
        if flag:
            drifted += 1
        print(
            f"{ex:<12} {day:<12} {len(vals):>4} {vmin:16.6f} {vmax:16.6f} "
            f"{ratio:12.6%} {cv:10.6%} {'YES' if flag else 'no':>8}"
        )

    print(
        f"\nsamples_file={SAMPLES_PATH} | groups={len(buckets)} | "
        f"drifted(>{threshold:.2%})={drifted}"
    )
    print(
        "决策门：漂移结果仅供人工判断；不自动改冻结/refresh 策略。"
        "若要改代码须明确下令。"
    )
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sample", action="store_true", help="拉一次 API 并追加 jsonl（默认）")
    p.add_argument("--summary", action="store_true", help="汇总已有采样的 max/min 漂移")
    p.add_argument(
        "--threshold",
        type=float,
        default=DRIFT_THRESHOLD,
        help=f"漂移阈值 max/min-1，默认 {DRIFT_THRESHOLD}",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.summary and not args.sample:
        raise SystemExit(summarize(args.threshold))
    if args.summary:
        sample_once()
        raise SystemExit(summarize(args.threshold))
    sample_once()


if __name__ == "__main__":
    main()
