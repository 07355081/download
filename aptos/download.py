# -*- coding: utf-8 -*-
"""Aptos 稳定币市值 + 周手续费销毁（免费，不占 Dune）。

稳定币：DefiLlama stablecoincharts/Aptos
手续费：DefiLlama summary/fees/aptos（费用全销毁）÷ APT 日价 → APT 枚数
价格：CoinGecko market_chart；失败则 coins.llama.fi/chart

不含月活（无免费同源）。

输出：
  output/json/aptos.json
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "aptos.json"
UA = "dashboard-data/aptos"
TIMEOUT = 90
ATTEMPTS = 3


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get_json(url: str) -> Any:
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < ATTEMPTS:
                time.sleep(min(2**attempt, 12))
    raise RuntimeError(f"GET {url} failed: {last}")


def day_of(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def as_ts(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def parse_stable(rows: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = as_ts(row.get("date"))
        tc = row.get("totalCirculatingUSD") or row.get("totalCirculating") or {}
        if ts is None:
            continue
        if isinstance(tc, dict):
            val = tc.get("peggedUSD") or tc.get("USD")
        else:
            val = tc
        if isinstance(val, (int, float)):
            out[day_of(ts)] = float(val)
    return out


def parse_fees(doc: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    chart = doc.get("totalDataChart") if isinstance(doc, dict) else None
    if not isinstance(chart, list):
        return out
    for pt in chart:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2 and isinstance(pt[0], (int, float)):
            if isinstance(pt[1], (int, float)):
                out[day_of(int(pt[0]))] = float(pt[1])
    return out


def fetch_apt_prices() -> dict[str, float]:
    out: dict[str, float] = {}
    # 免费无钥常用 days=365；max 常要 Demo/Pro key
    for days in ("365", "max"):
        try:
            cg = http_get_json(
                "https://api.coingecko.com/api/v3/coins/aptos/market_chart"
                f"?vs_currency=usd&days={days}&interval=daily"
            )
            for ts, price in cg.get("prices") or []:
                if isinstance(ts, (int, float)) and isinstance(price, (int, float)):
                    out[day_of(int(ts / 1000))] = float(price)
            if out:
                print(f"  prices from coingecko days={days} n={len(out)}")
                return out
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] coingecko days={days}: {exc}")

    start = int((datetime.now(timezone.utc) - timedelta(days=400)).timestamp())
    for span in (400, 200, 100):
        try:
            chart = http_get_json(
                f"https://coins.llama.fi/chart/coingecko:aptos?start={start}&span={span}&period=1d"
            )
            prices = ((chart.get("coins") or {}).get("coingecko:aptos") or {}).get("prices") or []
            for row in prices:
                if not isinstance(row, dict):
                    continue
                ts, price = row.get("timestamp"), row.get("price")
                if isinstance(ts, (int, float)) and isinstance(price, (int, float)):
                    out[day_of(int(ts))] = float(price)
            if out:
                print(f"  prices from llama span={span} n={len(out)}")
                return out
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] llama chart span={span}: {exc}")
    return out


def nearest_price(px: dict[str, float], day: str) -> float | None:
    if day in px:
        return px[day]
    earlier = [d for d in px if d <= day]
    return px[max(earlier)] if earlier else None


def week_monday(day: str) -> str:
    d = datetime.strptime(day, "%Y-%m-%d").date()
    return (d - timedelta(days=d.weekday())).isoformat()


def chart_series(label: str, label_en: str, series: dict[str, float], *, unit: str = "usd") -> dict[str, Any] | None:
    months: list[dict[str, Any]] = []
    prev: float | None = None
    for day in sorted(series):
        val = series[day]
        mom = None if prev in (None, 0) else round((val / float(prev) - 1.0) * 100.0, 4)
        months.append({"month": day, "usd": val, "mom_pct": mom, "unit": unit})
        prev = val
    if len(months) < 2:
        return None
    return {"label": label, "labelEn": label_en, "months": months}


def main() -> None:
    print("[aptos] fetch stablecoincharts/Aptos…")
    stables = parse_stable(http_get_json("https://stablecoins.llama.fi/stablecoincharts/Aptos"))
    if not stables:
        raise RuntimeError("Aptos 稳定币序列为空")
    time.sleep(0.3)
    print("[aptos] fetch summary/fees/aptos…")
    fees_usd = parse_fees(http_get_json("https://api.llama.fi/summary/fees/aptos?dataType=dailyFees"))
    time.sleep(0.3)
    print("[aptos] fetch APT prices…")
    px = fetch_apt_prices()
    if not px:
        raise RuntimeError("APT 价格为空")

    burn_apt: dict[str, float] = {}
    for day, usd in fees_usd.items():
        price = nearest_price(px, day)
        if price and price > 0:
            burn_apt[day] = usd / price

    weeks: dict[str, dict[str, float]] = {}
    buckets: dict[str, list[str]] = defaultdict(list)
    for day in sorted(burn_apt):
        buckets[week_monday(day)].append(day)
    for monday, days in sorted(buckets.items()):
        # 完整周：7 天都有销毁
        if len(days) < 7:
            continue
        weeks[monday] = {
            "apt": sum(burn_apt[d] for d in days),
            "usd": sum(fees_usd.get(d, 0.0) for d in days),
        }

    peak_mcap_day, peak_mcap = max(stables.items(), key=lambda x: x[1])
    latest_mcap_day = max(stables)
    latest_mcap = stables[latest_mcap_day]

    week_rows = [
        {"week": w, "burn_apt": v["apt"], "burn_usd": v["usd"]}
        for w, v in sorted(weeks.items())
    ]
    peak_week = max(week_rows, key=lambda r: r["burn_apt"]) if week_rows else None
    latest_week = week_rows[-1] if week_rows else None

    # 近 26 个完整周峰值（不含当前未完整周）
    half = week_rows[-26:] if len(week_rows) >= 26 else week_rows
    half_peak = max(half, key=lambda r: r["burn_apt"]) if half else None

    # 飞书页按美元画 series；周销毁是 APT 枚数，只放 fee_burn，避免被当成美元。
    series_blocks = [
        block
        for block in (chart_series("Aptos 稳定币市值", "Aptos stablecoin mcap", stables),)
        if block
    ]

    payload = {
        "code": "0",
        "msg": "success",
        "name": "aptos",
        "source": "defillama+coingecko",
        "source_url": "https://defillama.com/stablecoins/Aptos",
        "note_zh": (
            "稳定币为 DefiLlama 分链流通市值；手续费销毁为 DefiLlama Aptos 链日费（全销毁）"
            "按当日 APT 价折算枚数。口径可能与官网/推文略有差异。不含月活。"
        ),
        "updated_at": now_iso(),
        "stablecoin": {
            "as_of": latest_mcap_day,
            "market_cap_usd": latest_mcap,
            "peak_day": peak_mcap_day,
            "peak_usd": peak_mcap,
        },
        "fee_burn": {
            "latest_week": latest_week,
            "peak_week": peak_week,
            "half_year_peak_week": half_peak,
            "weeks": week_rows[-52:],
        },
        "history_mcap": [{"time": d, "market_cap": stables[d]} for d in sorted(stables)],
        "data": [{"time": d, "market_cap": stables[d]} for d in sorted(stables)],
        "series": series_blocks,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(OUT_PATH)
    lw = latest_week["burn_apt"] if latest_week else None
    print(
        f"[aptos] wrote {OUT_PATH} mcap={latest_mcap/1e8:.2f}亿 as_of={latest_mcap_day} "
        f"peak_mcap={peak_mcap_day} {peak_mcap/1e8:.2f}亿 "
        f"weeks={len(week_rows)} latest_week_apt={None if lw is None else round(lw)} "
        f"half_peak={None if not half_peak else round(half_peak['burn_apt'])}"
    )


if __name__ == "__main__":
    main()
