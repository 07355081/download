#!/usr/bin/env python3
"""一次性把月度大市值山寨补到 DefiLlama 还能给出完整月的最早处。

不进每日任务。已在缓存里的边界日不重打。某月比特币价或样本价不够就跳过，
连续三个月补不出就停。失败只影响这次回补。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import download as job


def main() -> None:
    job.load_env(job.ENV_PATH)
    now = datetime.now(timezone.utc)
    universe = job.build_universe()
    ids = ["bitcoin", *[str(coin["id"]) for coin in universe]]
    cache = job.load_cache()
    saved = job.load_saved_months()
    if saved:
        oldest = min(saved)
        year, month = job.add_months(int(oldest[:4]), int(oldest[5:7]), -1)
    else:
        year, month = job.add_months(now.year, now.month, -1)
    misses = 0
    added = 0
    while year > 2016:
        label = f"{year:04d}-{month:02d}"
        next_y, next_m = job.add_months(year, month, 1)
        start = f"{year:04d}-{month:02d}-01"
        end = f"{next_y:04d}-{next_m:02d}-01"
        if label not in saved:
            try:
                job.ensure_prices(cache, [start, end], ids)
                row = job.month_stats(cache, universe, label, start, end)
            except Exception as exc:  # noqa: BLE001
                print(f"  {label} 失败，跳过：{exc}")
                row = None
            if row is None:
                misses += 1
                if misses >= 3:
                    print(f"连续 {misses} 个月补不出，停在 {label}")
                    break
            else:
                misses = 0
                saved[label] = row
                added += 1
                if added % 6 == 0:
                    dest = job.write_monthly(now, universe, saved)
                    print(f"  checkpoint {dest} months={len(saved)}")
            time.sleep(0.35)
        year, month = job.add_months(year, month, -1)
    if added == 0 and len(saved) < 6:
        print("没有补进新月份，不覆盖旧文件")
        return
    dest = job.write_monthly(now, universe, saved)
    print(f"wrote {dest} added={added} months={len(saved)}")


if __name__ == "__main__":
    main()
