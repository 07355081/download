# -*- coding: utf-8 -*-
"""已上市加密交易所现货周度占比（Coinbase / Bullish / Kraken / Gemini）。

免费源：CoinGecko /exchanges/{id}/volume_chart（成交量单位为 BTC）。
按 UTC 自然周（周一至周日）加总后，在四所内重归一化为占比。
非 Kaiko；不含交易所股票与 BTC 相关性。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "output" / "json" / "listed_cex_spot_share.json"
ENV_PATH = ROOT / ".env"
GECKO = "https://api.coingecko.com/api/v3"
UA = "dashboard-data/listed-cex-spot-share"
TIMEOUT = 45
ATTEMPTS = 4
DAYS = 90
EXCHANGES = (
    ("gdax", "Coinbase"),
    ("bullish_com", "Bullish"),
    ("kraken", "Kraken"),
    ("gemini", "Gemini"),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        text = line.strip().lstrip("\ufeff")
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def gecko_key() -> str:
    return (
        os.environ.get("COINGECKO_DEMO_API_KEY")
        or os.environ.get("COINGECKO_KEY")
        or os.environ.get("COINGECKO_API_KEY")
        or ""
    ).strip()


def http_get_json(url: str) -> Any:
    headers = {"User-Agent": UA, "Accept": "application/json"}
    key = gecko_key()
    if key:
        headers["x-cg-demo-api-key"] = key
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = exc
            if attempt < ATTEMPTS and exc.code in (429, 500, 502, 503, 504):
                time.sleep(min(2**attempt, 20))
                continue
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < ATTEMPTS:
                time.sleep(min(2**attempt, 12))
                continue
            raise
    raise RuntimeError(f"GET {url} failed: {last}")


def volume_chart(exchange_id: str) -> dict[str, float]:
    raw = http_get_json(f"{GECKO}/exchanges/{exchange_id}/volume_chart?days={DAYS}")
    if not isinstance(raw, list):
        raise RuntimeError(f"{exchange_id} volume_chart 非列表: {raw}")
    out: dict[str, float] = {}
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        try:
            ms = float(row[0])
            vol = float(row[1])
        except (TypeError, ValueError):
            continue
        if vol < 0:
            continue
        day = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
        out[day] = vol
    return out


def week_end_sunday(day: str) -> str:
    dt = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    # Monday=0 … Sunday=6 → advance to Sunday
    return (dt + timedelta(days=(6 - dt.weekday()))).strftime("%Y-%m-%d")


def build_weeks(daily: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    names = [name for _, name in EXCHANGES]
    bucket: dict[str, dict[str, float]] = {}
    for name, series in daily.items():
        for day, vol in series.items():
            end = week_end_sunday(day)
            slot = bucket.setdefault(end, {n: 0.0 for n in names})
            slot[name] = slot.get(name, 0.0) + float(vol)

    # 丢掉未完整周：当前 UTC 周尚未结束则排除
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    current_end = week_end_sunday(today)
    rows: list[dict[str, Any]] = []
    for end in sorted(bucket):
        if end >= current_end:
            continue
        vols = {n: float(bucket[end].get(n) or 0.0) for n in names}
        total = sum(vols.values())
        if total <= 0:
            continue
        shares = {n: round(100.0 * vols[n] / total, 2) for n in names}
        ranked = sorted(shares.items(), key=lambda kv: -kv[1])
        leader, leader_share = ranked[0]
        second_share = ranked[1][1] if len(ranked) > 1 else 0.0
        gap = round(leader_share - second_share, 2)
        rows.append(
            {
                "time": end,
                "week_end": end,
                "value": gap,
                "gap_pp": gap,
                "leader": leader,
                "shares": shares,
                "vol_btc": {n: round(vols[n], 4) for n in names},
                "vol_btc_total": round(total, 4),
            }
        )
    return rows


def to_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = [name for _, name in EXCHANGES]
    out: list[dict[str, Any]] = []
    for name in names:
        months = []
        for row in rows:
            share = (row.get("shares") or {}).get(name)
            if isinstance(share, (int, float)):
                months.append({"month": row["time"], "usd": float(share)})
        out.append({"label": name, "labelEn": name, "slug": name.lower(), "months": months})
    return out


def main() -> int:
    load_env(ENV_PATH)
    daily: dict[str, dict[str, float]] = {}
    for eid, name in EXCHANGES:
        daily[name] = volume_chart(eid)
        print(f"[listed-cex-spot-share] {name} days={len(daily[name])}", flush=True)
        time.sleep(1.2)

    rows = build_weeks(daily)
    if len(rows) < 2:
        raise SystemExit("周度样本不足 2 周")

    latest = rows[-1]
    shares = latest["shares"]
    latest_top = [
        {
            "symbol": name,
            "name": name,
            "market_cap": float(shares[name]),
            "share_pct": float(shares[name]),
        }
        for name, _ in sorted(shares.items(), key=lambda kv: -kv[1])
    ]

    doc = {
        "name": "listed_cex_spot_share",
        "source": "CoinGecko exchanges volume_chart",
        "source_url": "https://www.coingecko.com/en/exchanges",
        "note": (
            "Coinbase/Bullish/Kraken/Gemini 四周现货成交（CG volume_chart，BTC 量）"
            "按 UTC 自然周加总后在四所内重归一化占比。非 Kaiko；不含股价相关性。"
        ),
        "updated_at": now_iso(),
        "as_of": latest["time"],
        "universe": [name for _, name in EXCHANGES],
        "latest": latest,
        "data": rows,
        "series": to_series(rows),
        "latest_top": latest_top,
        "meta": {
            "as_of": latest["time"],
            "updated_at": now_iso(),
            "weeks": len(rows),
            "days_lookback": DAYS,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT)
    print(
        f"[listed-cex-spot-share] week={latest['time']} leader={latest['leader']} "
        f"gap={latest['gap_pp']}pp shares={shares} n={len(rows)} → {OUT}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
