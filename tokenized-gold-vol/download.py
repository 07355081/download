#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代币化黄金现货成交（CoinGecko 免费）。

口径：PAXG（pax-gold）+ XAUT（tether-gold）日成交额加总。
不是 CoinGecko 研报整板块同源，数字可能对不上外电。
日线并入本地，避免免费接口窗口滑掉。

用法：
  python download.py
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_PATH = HERE / "output" / "json" / "tokenized_gold_vol.json"
UA = "dashboard-data/tokenized-gold-vol"
SLEEP_S = 1.6
COINS = (
    ("pax-gold", "PAXG"),
    ("tether-gold", "XAUT"),
)
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("COINGECKO_KEY", "COINGECKO_DEMO_API_KEY", "COINGECKO_API_KEY"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def cg_key(env: dict[str, str]) -> str:
    return (
        env.get("COINGECKO_KEY")
        or env.get("COINGECKO_DEMO_API_KEY")
        or env.get("COINGECKO_API_KEY")
        or ""
    ).strip()


def http_json(url: str, headers: dict[str, str] | None = None) -> Any:
    req = urllib.request.Request(
        url,
        headers={**(headers or {}), "User-Agent": UA, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cg_get(path: str, params: dict[str, Any], env: dict[str, str]) -> Any:
    qs = urllib.parse.urlencode(params)
    key = cg_key(env)
    attempts: list[tuple[str, dict[str, str]]] = []
    if key:
        attempts.append(
            (f"https://api.coingecko.com/api/v3{path}?{qs}", {"x-cg-demo-api-key": key})
        )
        attempts.append(
            (f"https://pro-api.coingecko.com/api/v3{path}?{qs}", {"x-cg-pro-api-key": key})
        )
    attempts.append((f"https://api.coingecko.com/api/v3{path}?{qs}", {}))
    last: Exception | None = None
    for url, headers in attempts:
        for attempt in range(4):
            try:
                return http_json(url, headers)
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code in (429, 503) and attempt < 3:
                    time.sleep(3.0 * (attempt + 1))
                    continue
                # 401/400：换下一组密钥或免密钥
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last = exc
                break
    raise RuntimeError(f"CoinGecko {path} 失败: {last}")


def load_prior() -> dict[str, dict[str, float]]:
    if not OUT_PATH.is_file():
        return {}
    try:
        doc = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    days = doc.get("days") if isinstance(doc, dict) else None
    if not isinstance(days, list):
        return {}
    out: dict[str, dict[str, float]] = {}
    for row in days:
        if not isinstance(row, dict) or not row.get("day"):
            continue
        day = str(row["day"])[:10]
        slot = out.setdefault(day, {})
        for key in ("paxg", "xaut", "total"):
            if isinstance(row.get(key), (int, float)):
                slot[key] = float(row[key])
    return out


def chart_volumes(coin_id: str, env: dict[str, str]) -> dict[str, float]:
    # 免费接口 days=max 常 401；365 天够滚动并入本地。
    payload = cg_get(
        f"/coins/{coin_id}/market_chart",
        {"vs_currency": "usd", "days": "365"},
        env,
    )
    series = payload.get("total_volumes") if isinstance(payload, dict) else None
    out: dict[str, float] = {}
    if not isinstance(series, list):
        return out
    for point in series:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        if not isinstance(point[0], (int, float)) or not isinstance(point[1], (int, float)):
            continue
        if point[1] < 0:
            continue
        day = datetime.fromtimestamp(point[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        out[day] = float(point[1])
    return out


def quarter_of(day: date) -> tuple[int, int]:
    return day.year, (day.month - 1) // 3 + 1


def quarter_bounds(year: int, quarter: int) -> tuple[date, date]:
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    start = date(year, start_month, 1)
    end = date(year, end_month, monthrange(year, end_month)[1])
    return start, end


def sum_range(days: dict[str, dict[str, float]], start: date, end: date) -> dict[str, float]:
    paxg = xaut = 0.0
    n = 0
    for text, row in days.items():
        day = datetime.strptime(text, "%Y-%m-%d").date()
        if start <= day <= end:
            paxg += float(row.get("paxg") or 0)
            xaut += float(row.get("xaut") or 0)
            n += 1
    return {"paxg": round(paxg, 2), "xaut": round(xaut, 2), "total": round(paxg + xaut, 2), "days": float(n)}


def main() -> int:
    env = load_env()
    days = load_prior()
    for coin_id, label in COINS:
        print(f"[tokenized-gold-vol] fetch {label} ({coin_id})")
        vols = chart_volumes(coin_id, env)
        field = "paxg" if label == "PAXG" else "xaut"
        for day, value in vols.items():
            days.setdefault(day, {})[field] = value
        time.sleep(SLEEP_S)
    for day, row in days.items():
        row["total"] = float(row.get("paxg") or 0) + float(row.get("xaut") or 0)

    today = datetime.now(timezone.utc).date()
    day_rows = [
        {
            "day": day,
            "paxg": round(float(row.get("paxg") or 0), 2),
            "xaut": round(float(row.get("xaut") or 0), 2),
            "total": round(float(row.get("total") or 0), 2),
        }
        for day, row in sorted(days.items())
    ]
    years = sorted({datetime.strptime(day, "%Y-%m-%d").date().year for day in days})
    year_rows = []
    for year in years:
        start, end = date(year, 1, 1), date(year, 12, 31)
        if end >= today:
            end = today - timedelta(days=1)
        if end < start:
            continue
        stats = sum_range(days, start, end)
        year_rows.append({"year": year, **stats, "complete": end == date(year, 12, 31)})

    quarters = []
    for year in years:
        for quarter in range(1, 5):
            start, end = quarter_bounds(year, quarter)
            if end >= today:
                continue
            stats = sum_range(days, start, end)
            span = (end - start).days + 1
            if stats["days"] < span - 3:
                continue
            quarters.append({"year": year, "quarter": quarter, "end": end.isoformat(), **stats})

    payload = {
        "updated_at": now_iso(),
        "source": "CoinGecko market_chart total_volumes：pax-gold + tether-gold",
        "note": (
            "现货成交近似为 PAXG 与 XAUT 日成交额加总，不是 CoinGecko 研报整板块。"
            "只发最新完整季度：季度成交高于上一完整自然年，或创已存季度新高。"
        ),
        "coins": [{"id": cid, "symbol": sym} for cid, sym in COINS],
        "latest_quarter": quarters[-1] if quarters else None,
        "quarters": quarters,
        "years": year_rows,
        "days": day_rows,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    latest = quarters[-1] if quarters else None
    if latest:
        print(
            f"[tokenized-gold-vol] wrote {OUT_PATH} days={len(day_rows)} "
            f"quarters={len(quarters)} latest={latest['year']}Q{latest['quarter']} "
            f"total={latest['total']:.0f}"
        )
    else:
        print(f"[tokenized-gold-vol] wrote {OUT_PATH} days={len(day_rows)} quarters=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
