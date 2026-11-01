#!/usr/bin/env python3
"""用已经落盘的名单和免费历史价，补上被删掉的山寨前 50 日文件。

不进每日任务。已有的日子不重打。价补不齐的日子留空。
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone

import download as job


def one_day(cache, universe, ath_day, label, ids):
    delay = 20
    last = "unknown"
    for _ in range(6):
        try:
            job.ensure_prices(cache, [ath_day, label], ids)
            return job.window_stats(cache, universe, ath_day, label), None
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
            if "429" not in last and "Too Many" not in last:
                return None, last
            print(f"  {label} 限速，{delay}s 后再试")
            time.sleep(delay)
            delay = min(delay * 2, 180)
    return None, last


def main() -> None:
    job.load_env(job.ENV_PATH)
    if "--series-only" in sys.argv:
        print(f"series={job.write_series()}")
        return
    if not job.LATEST_PATH.is_file():
        print("没有 latest 文件，不回补")
        return
    latest = json.loads(job.LATEST_PATH.read_text(encoding="utf-8"))
    universe = latest.get("constituents") if isinstance(latest, dict) else None
    ath_day = str(latest.get("ath_day") or "") if isinstance(latest, dict) else ""
    if not isinstance(universe, list) or len(universe) < job.MIN_PRICED or len(ath_day) != 10:
        print("latest 里没有名单或 ATH 日，不回补")
        return
    have = {path.stem for path in job.HISTORY_DIR.glob("????-??-??.json")} if job.HISTORY_DIR.is_dir() else set()
    start = datetime.strptime(ath_day, "%Y-%m-%d").date()
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    ids = ["bitcoin", *[str(coin.get("id")) for coin in universe if isinstance(coin, dict) and coin.get("id")]]
    cache = job.load_cache()
    written = 0
    skipped = 0
    day = start
    while day <= end:
        label = day.isoformat()
        day += timedelta(days=1)
        if label in have or label == ath_day:
            continue
        stats, err = one_day(cache, universe, ath_day, label, ids)
        if err or stats is None:
            skipped += 1
            print(f"  {label} 留空：{err}")
            time.sleep(2)
            continue
        stats.pop("constituents_priced", None)
        payload = {
            "code": "0",
            "msg": "success",
            "name": "alt_top50_btc_ath",
            "description": latest.get("description"),
            "source": latest.get("source"),
            "backfill": "defillama-historical-prices",
            "updated_at": job.now_utc_iso(),
            "day": label,
            "ath_day": ath_day,
            "end_day": label,
            "target_n": latest.get("target_n"),
            **stats,
        }
        job.write_json(job.HISTORY_DIR / f"{label}.json", payload)
        written += 1
        time.sleep(1.2)
        if written % 30 == 0:
            print(f"  wrote {written} skipped {skipped} through {label}")
    series_n = job.write_series()
    print(f"backfill days={written} skipped={skipped} series={series_n}")


if __name__ == "__main__":
    main()
