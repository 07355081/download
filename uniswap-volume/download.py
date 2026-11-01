"""Uniswap v2/v3/v4 daily DEX volume from DefiLlama (free, no Dune).

Writes a small JSON with daily rows plus the latest complete UTC rolling-30d
snapshot. Incomplete UTC today is excluded from the rolling window.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
JSON_DIR = HERE / "output" / "json"
OUT_PATH = JSON_DIR / "uniswap_version_volume.json"

SLUGS = (
    ("v2", "uniswap-v2"),
    ("v3", "uniswap-v3"),
    ("v4", "uniswap-v4"),
)
SUMMARY_URL = (
    "https://api.llama.fi/summary/dexs/{slug}"
    "?excludeTotalDataChartBreakdown=true&dataType=dailyVolume"
)
ATTEMPTS = 3
TIMEOUT = 60
WINDOW = 30


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get_json(url: str) -> dict:
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "dashboard-data/uniswap-volume"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < ATTEMPTS:
                time.sleep(min(2**attempt, 12))
    raise RuntimeError(f"GET {url} failed: {last}")


def chart_from_payload(payload: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for point in payload.get("totalDataChart") or []:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        ts, value = point[0], point[1]
        if not isinstance(ts, (int, float)) or not isinstance(value, (int, float)):
            continue
        day = datetime.fromtimestamp(int(ts), timezone.utc).date().isoformat()
        out[day] = float(value)
    return out


def fetch_slug(slug: str) -> dict[str, float]:
    url = SUMMARY_URL.format(slug=slug)
    return chart_from_payload(http_get_json(url))


def daterange(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def rolling_window(series: dict[str, dict[str, float]], end: date) -> dict | None:
    start = end - timedelta(days=WINDOW - 1)
    totals = {key: 0.0 for key, _ in SLUGS}
    days_present = {key: 0 for key, _ in SLUGS}
    for day in daterange(start, end):
        iso = day.isoformat()
        for key, _ in SLUGS:
            value = series[key].get(iso)
            if value is None:
                continue
            totals[key] += float(value)
            days_present[key] += 1
    # Require each version to cover most of the window; v4 history is shorter early on.
    if min(days_present.values()) < WINDOW - 3:
        return None
    total = sum(totals.values())
    if total <= 0:
        return None
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "v2": round(totals["v2"], 2),
        "v3": round(totals["v3"], 2),
        "v4": round(totals["v4"], 2),
        "total": round(total, 2),
        "share_v2": round(totals["v2"] / total, 6),
        "share_v3": round(totals["v3"] / total, 6),
        "share_v4": round(totals["v4"] / total, 6),
        "days": {key: days_present[key] for key, _ in SLUGS},
    }


def build(series: dict[str, dict[str, float]]) -> dict:
    today = datetime.now(timezone.utc).date()
    end = today - timedelta(days=1)
    all_days = sorted(
        {
            day
            for key, _ in SLUGS
            for day in series[key]
        }
    )
    daily = []
    for day in all_days:
        if day > end.isoformat():
            continue
        row = {"date": day}
        total = 0.0
        for key, _ in SLUGS:
            value = series[key].get(day)
            if value is None:
                row[key] = None
            else:
                row[key] = round(float(value), 2)
                total += float(value)
        row["total"] = round(total, 2) if total > 0 else None
        daily.append(row)

    latest = rolling_window(series, end)
    # Recent windows for threshold crossing checks on the dashboard side.
    windows = []
    for back in range(0, 120):
        win_end = end - timedelta(days=back)
        win = rolling_window(series, win_end)
        if win:
            windows.append(win)

    return {
        "code": "0",
        "msg": "success",
        "name": "uniswap_version_volume",
        "description": (
            "DefiLlama Uniswap v2/v3/v4 日成交量（USD）。"
            "rolling_30d 为最近一个完整 UTC 日为末日的 30 日合计；不含当日未完结。"
            "数字是本序列自加总，不是官方推文口径。"
        ),
        "source": "defillama",
        "source_url": "https://defillama.com/protocol/uniswap",
        "updated_at": now_iso(),
        "versions": [key for key, _ in SLUGS],
        "window_days": WINDOW,
        "daily": daily,
        "rolling_30d": latest,
        "rolling_30d_history": windows,
    }


def main() -> int:
    series: dict[str, dict[str, float]] = {}
    for key, slug in SLUGS:
        print(f"GET {slug}")
        try:
            series[key] = fetch_slug(slug)
        except Exception as exc:  # noqa: BLE001
            print(f"failed {slug}: {exc}")
            return 1
        print(f"  points={len(series[key])}")

    payload = build(series)
    if not payload.get("rolling_30d"):
        print("rolling_30d missing, refuse to publish")
        return 1

    JSON_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    latest = payload["rolling_30d"]
    print(
        f"wrote {OUT_PATH} daily={len(payload['daily'])} "
        f"end={latest['end']} total={latest['total'] / 1e8:.2f}亿 "
        f"v4={latest['share_v4'] * 100:.1f}% v3={latest['share_v3'] * 100:.1f}% "
        f"v2={latest['share_v2'] * 100:.1f}%"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
