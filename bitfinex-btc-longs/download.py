"""Bitfinex 比特币保证金多头。公开 stats1，不占付费额度。

分钟序列只在每个 UTC 日取第一笔，避免把全量分钟点拉回 VPS。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)

HERE = Path(__file__).resolve().parent
OUT = HERE / "output" / "json"
DAILY_PATH = OUT / "daily.json"
LATEST_PATH = OUT / "longs.json"
BASE = "https://api-pub.bitfinex.com/v2/stats1/pos.size:1m:tBTCUSD:long"
START = datetime(2023, 12, 1, tzinfo=timezone.utc)
UA = {"User-Agent": "dashboard-data/bitfinex-longs"}
SLEEP = 1.1


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_ms(day: datetime) -> int:
    stamp = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return int(stamp.timestamp() * 1000)


def get_json(url: str):
    last: Exception | None = None
    for attempt in range(1, 7):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(15 * attempt)
    raise RuntimeError(last)


def fetch_chunk(start_ms: int) -> list:
    url = f"{BASE}/hist?start={start_ms}&limit=10000&sort=1"
    rows = get_json(url)
    time.sleep(SLEEP)
    return rows if isinstance(rows, list) else []


def load_daily() -> dict[str, float]:
    if not DAILY_PATH.is_file():
        return {}
    doc = json.loads(DAILY_PATH.read_text(encoding="utf-8"))
    rows = doc.get("rows") if isinstance(doc, dict) else None
    out: dict[str, float] = {}
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and row.get("day") and isinstance(row.get("btc"), (int, float)):
                out[str(row["day"])] = float(row["btc"])
    return out


def save_daily(rows: dict[str, float]) -> None:
    payload = {
        "rows": [{"day": day, "btc": rows[day]} for day in sorted(rows)],
        "updated_at": now_iso(),
    }
    DAILY_PATH.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")


def nearest(rows: dict[str, float], day: str, forward: bool) -> float | None:
    keys = sorted(rows)
    if day in rows:
        return rows[day]
    if forward:
        later = [key for key in keys if key >= day]
        return rows[later[0]] if later else None
    earlier = [key for key in keys if key <= day]
    return rows[earlier[-1]] if earlier else None


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    daily = load_daily()
    start_ms = day_ms(START)
    if daily:
        last_day = datetime.strptime(max(daily), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        start_ms = day_ms(last_day) + 60_000
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    chunk_fails = 0
    while start_ms < now_ms:
        try:
            rows = fetch_chunk(start_ms)
        except Exception as exc:  # noqa: BLE001
            chunk_fails += 1
            print(f"chunk retry {chunk_fails} {start_ms}: {exc}", flush=True)
            if chunk_fails >= 8:
                print("chunk give up, keep latest", flush=True)
                break
            time.sleep(30)
            continue
        chunk_fails = 0
        if not rows:
            break
        last_ts = start_ms
        for row in rows:
            if not isinstance(row, list) or len(row) < 2:
                continue
            last_ts = int(row[0])
            day = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc).date().isoformat()
            daily.setdefault(day, float(row[1]))
        save_daily(daily)
        print(f"chunk days={len(daily)} last={last_ts}", flush=True)
        if last_ts <= start_ms or len(rows) < 10000:
            break
        start_ms = last_ts + 60_000
    last = get_json(f"{BASE}/last")
    if not isinstance(last, list) or len(last) < 2:
        raise SystemExit(f"unexpected last: {last}")
    latest_ms = int(last[0])
    latest_btc = float(last[1])
    latest_time = datetime.fromtimestamp(latest_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    latest_day = datetime.fromtimestamp(latest_ms / 1000, tz=timezone.utc).date().isoformat()
    daily[latest_day] = latest_btc
    save_daily(daily)
    history = [btc for day, btc in daily.items() if day < latest_day and day >= "2023-12-01"]
    prior = max(history) if history else None
    year = latest_day[:4] + "-01-01"
    week = (datetime.strptime(latest_day, "%Y-%m-%d").date() - timedelta(days=7)).isoformat()
    payload = {
        "source": BASE,
        "note": "Bitfinex 公开持仓统计。不是 True Market Mean，也不是 200 日均线。",
        "updated_at": now_iso(),
        "latest": {"time": latest_time, "btc": round(latest_btc, 4)},
        "prior_high_btc": None if prior is None else round(prior, 4),
        "is_high_since_2023_12": prior is None or latest_btc >= prior,
        "ytd_btc": nearest(daily, year, True),
        "week_ago_btc": nearest(daily, week, False),
        "sample": "分钟统计里每个 UTC 日保留第一笔，最新值用 last",
    }
    LATEST_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"latest {latest_btc:.2f} high_since_2023_12={payload['is_high_since_2023_12']} days={len(daily)}"
    )


if __name__ == "__main__":
    main()
