# -*- coding: utf-8 -*-
"""美现货比特币 ETF 日成交额（Yahoo Finance 免费）。

对各 ETF：日成交量 × 收盘价 ≈ 美元成交额，再加总。
另算 30 日移动平均，及相对序列峰值的回撤。
非 Glassnode 同源；周末/美股休市无点。

输出：output/json/btc_etf_volume.json

用法：
  python download.py
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "btc_etf_volume.json"
UA = "dashboard-data/btc-etf-volume"
TIMEOUT = 40
ATTEMPTS = 4
# 与看板/CoinGlass 美现货 BTC ETF 常见名单对齐（Yahoo 可报价）
TICKERS = (
    "IBIT",
    "FBTC",
    "GBTC",
    "ARKB",
    "BITB",
    "BTCO",
    "EZBC",
    "BRRR",
    "HODL",
    "BTCW",
    "BTC",
    "MSBT",
)
MA_WINDOW = 30
TZ_NY = ZoneInfo("America/New_York")
TZ_UTC = timezone.utc


def now_iso() -> str:
    return datetime.now(TZ_UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get_json(url: str) -> Any:
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
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
    raise RuntimeError(f"GET failed: {last}")


def fetch_ticker(ticker: str) -> dict[str, dict[str, float]]:
    """day -> {close, volume, volume_usd}"""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?interval=1d&range=2y&events=div%7Csplit"
    )
    doc = http_get_json(url)
    result = (doc.get("chart") or {}).get("result") or []
    if not result:
        raise RuntimeError(f"{ticker}: empty chart")
    res = result[0]
    ts = res.get("timestamp") or []
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    out: dict[str, dict[str, float]] = {}
    for i, raw_ts in enumerate(ts):
        if i >= len(closes) or i >= len(volumes):
            break
        c, v = closes[i], volumes[i]
        if c is None or v is None:
            continue
        close = float(c)
        vol = float(v)
        if close <= 0 or vol < 0:
            continue
        day = datetime.fromtimestamp(int(raw_ts), tz=TZ_NY).strftime("%Y-%m-%d")
        out[day] = {"close": close, "volume": vol, "volume_usd": close * vol}
    return out


def last_complete_ny_day() -> str:
    now = datetime.now(TZ_NY)
    # 美股常规收盘 16:00；收盘前不用当日未完结 K
    if now.weekday() < 5 and (now.hour, now.minute) < (16, 5):
        now = now - timedelta(days=1)
    while now.weekday() >= 5:
        now = now - timedelta(days=1)
    return now.strftime("%Y-%m-%d")


def moving_avg(values: list[float | None], window: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        chunk = values[i - window + 1 : i + 1] if i + 1 >= window else []
        nums = [x for x in chunk if isinstance(x, (int, float))]
        out.append(sum(nums) / len(nums) if len(nums) == window else None)
    return out


def main() -> None:
    per_ticker: dict[str, dict[str, dict[str, float]]] = {}
    for i, ticker in enumerate(TICKERS):
        try:
            per_ticker[ticker] = fetch_ticker(ticker)
            print(f"  {ticker}: {len(per_ticker[ticker])} days")
        except Exception as exc:  # noqa: BLE001
            print(f"  [WARN] {ticker}: {exc}")
            per_ticker[ticker] = {}
        if i + 1 < len(TICKERS):
            time.sleep(0.35)

    all_days = sorted({d for series in per_ticker.values() for d in series})
    cutoff = last_complete_ny_day()
    all_days = [d for d in all_days if d <= cutoff]
    if not all_days:
        raise RuntimeError("无可用交易日")

    rows: list[dict[str, Any]] = []
    totals: list[float | None] = []
    for day in all_days:
        by_ticker: dict[str, float] = {}
        total = 0.0
        ok = 0
        for ticker in TICKERS:
            point = per_ticker.get(ticker, {}).get(day)
            if not point:
                continue
            usd = float(point["volume_usd"])
            by_ticker[ticker] = usd
            total += usd
            ok += 1
        if ok == 0:
            totals.append(None)
            continue
        totals.append(total)
        rows.append(
            {
                "time": day,
                "volume_usd": total,
                "ticker_count": ok,
                "by_ticker": by_ticker,
            }
        )

    # 只保留有合计的行，并对齐 MA
    volume_series = [float(r["volume_usd"]) for r in rows]
    ma_list = moving_avg(volume_series, MA_WINDOW)
    peak_ma = None
    for i, row in enumerate(rows):
        ma = ma_list[i]
        row["ma30_usd"] = ma
        if isinstance(ma, (int, float)):
            if peak_ma is None or ma > peak_ma:
                peak_ma = float(ma)
            row["peak_ma30_usd"] = peak_ma
            row["vs_peak_pct"] = (float(ma) - peak_ma) / peak_ma * 100.0 if peak_ma else None
        else:
            row["peak_ma30_usd"] = peak_ma
            row["vs_peak_pct"] = None

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "meta": {
            "source": "yahoo_finance",
            "tickers": list(TICKERS),
            "ma_window": MA_WINDOW,
            "note": (
                "各美现货 BTC ETF 日成交量×收盘价加总；30 日均相对本序列峰值回撤。"
                "近似口径，非 Glassnode。"
            ),
            "updated_at": now_iso(),
            "as_of": cutoff,
        },
        "data": rows,
    }
    OUT_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    latest = rows[-1]
    ma = latest.get("ma30_usd")
    vs = latest.get("vs_peak_pct")
    print(
        f"[btc-etf-volume] as_of={cutoff} day={latest['time']} "
        f"vol={latest['volume_usd']/1e8:.2f}亿 "
        f"ma30={'n/a' if ma is None else f'{float(ma)/1e8:.2f}亿'} "
        f"vs_peak={'n/a' if vs is None else f'{float(vs):+.1f}%'} "
        f"n={len(rows)} → {OUT_PATH}"
    )


if __name__ == "__main__":
    main()
