"""年初至今：持有人收入折成代币，对照流通量变化。

免费来源：
  DefiLlama GET /summary/fees/{slug}?dataType=dailyHoldersRevenue
  CoinGecko market_chart 的市值/价格 ≈ 流通量

这不是 Tokenomist 回购台账。Rollbit 没有免费持有人收入序列，记为缺失。

输出：
  output/json/ytd.json
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_PATH = HERE / "output" / "json" / "ytd.json"
UA = "dashboard-data/token-buyback-supply"
TIMEOUT = 60
SLEEP = 0.35
GECKO_GAP = 2.2

# symbol, name, llama slug, coingecko id
WATCH: list[tuple[str, str, str, str]] = [
    ("MET", "Meteora", "meteora", "meteora"),
    ("PUMP", "Pump", "pump", "pump-fun"),
    ("GMX", "GMX", "gmx", "gmx"),
    ("MPLX", "Metaplex", "metaplex", "metaplex"),
    ("HYPE", "Hyperliquid", "hyperliquid", "hyperliquid"),
    ("LIT", "Lighter", "lighter", "lighter"),
    ("AAVE", "Aave", "aave", "aave"),
]
MISSING = [("RLB", "Rollbit", "DefiLlama 没有 rollbit 的持有人收入")]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env() -> None:
    for path in (ROOT / ".env", ROOT.parent / "data-dashboard" / ".env.local", ROOT.parent / "data-dashboard" / ".env.live"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def http_json(url: str, headers: dict[str, str] | None = None) -> Any:
    req_headers = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    last: Exception | None = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(min(2**attempt, 8))
    raise RuntimeError(f"GET failed: {last}")


def day_of(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def holders_daily(slug: str) -> dict[str, float]:
    url = f"https://api.llama.fi/summary/fees/{slug}?dataType=dailyHoldersRevenue"
    print(f"  llama {slug}")
    doc = http_json(url)
    out: dict[str, float] = {}
    chart = doc.get("totalDataChart") if isinstance(doc, dict) else None
    if not isinstance(chart, list):
        return out
    for pt in chart:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        if isinstance(pt[0], (int, float)) and isinstance(pt[1], (int, float)):
            out[day_of(int(pt[0]))] = float(pt[1])
    time.sleep(SLEEP)
    return out


class Gecko:
    def __init__(self) -> None:
        self._last = 0.0
        key = (os.environ.get("COINGECKO_DEMO_API_KEY") or os.environ.get("COINGECKO_KEY") or "").strip()
        self._headers = {"x-cg-demo-api-key": key} if key else None

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if self._last and elapsed < GECKO_GAP:
            time.sleep(GECKO_GAP - elapsed)
        self._last = time.monotonic()

    def chart(self, coin_id: str) -> dict[str, dict[str, float]]:
        self._wait()
        print(f"  gecko {coin_id}")
        payload = http_json(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart?vs_currency=usd&days=365",
            self._headers,
        )
        by_day: dict[str, dict[str, float]] = {}
        if not isinstance(payload, dict):
            return by_day
        for key, field in (("prices", "price"), ("market_caps", "market_cap")):
            series = payload.get(key) or []
            if not isinstance(series, list):
                continue
            for point in series:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                if not isinstance(point[0], (int, float)) or not isinstance(point[1], (int, float)):
                    continue
                if point[1] <= 0:
                    continue
                day = datetime.fromtimestamp(point[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                by_day.setdefault(day, {})[field] = float(point[1])
        return by_day


def nearest_price(chart: dict[str, dict[str, float]], day: str) -> float | None:
    row = chart.get(day) or {}
    price = row.get("price")
    if price:
        return price
    base = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    for delta in range(1, 4):
        prev = (base - timedelta(days=delta)).strftime("%Y-%m-%d")
        price = (chart.get(prev) or {}).get("price")
        if price:
            return price
    return None


def supply_on(chart: dict[str, dict[str, float]], day: str) -> float | None:
    row = chart.get(day) or {}
    price = row.get("price")
    mcap = row.get("market_cap")
    if price and mcap and price > 0:
        return mcap / price
    return None


def build_row(
    symbol: str,
    name: str,
    slug: str,
    gecko_id: str,
    daily: dict[str, float],
    chart: dict[str, dict[str, float]],
    start: str,
    end: str,
) -> dict[str, Any]:
    usd_days = sorted(day for day, val in daily.items() if start <= day <= end and val > 0)
    tokens = 0.0
    priced = 0
    for day in usd_days:
        price = nearest_price(chart, day)
        if not price:
            continue
        tokens += daily[day] / price
        priced += 1
    supply_days = sorted(
        day for day in chart if start <= day <= end and supply_on(chart, day)
    )
    distinct_supply = len({round(supply_on(chart, day) or 0, 2) for day in supply_days})
    supply_start = supply_on(chart, supply_days[0]) if supply_days else None
    supply_end = supply_on(chart, supply_days[-1]) if supply_days else None
    growth = None
    if supply_start is not None and supply_end is not None:
        growth = supply_end - supply_start
    start_day = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_day = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    window_days = max((end_day - start_day).days + 1, 1)
    # 有收入的日子本身都能标价，不能当成全年都有数。序列要从年初附近覆盖到窗口末。
    covers_window = priced / window_days >= 0.7
    starts_near_january = bool(usd_days) and usd_days[0] <= (start_day + timedelta(days=31)).strftime("%Y-%m-%d")
    ends_near_window = bool(usd_days) and usd_days[-1] >= (end_day - timedelta(days=7)).strftime("%Y-%m-%d")
    complete = (
        covers_window
        and starts_near_january
        and ends_near_window
        and bool(supply_start)
        and supply_end is not None
        and growth is not None
        and distinct_supply >= 10
    )
    exceeds = bool(complete and tokens > growth)
    usd = sum(daily[day] for day in usd_days)
    return {
        "symbol": symbol,
        "name": name,
        "llama_slug": slug,
        "gecko_id": gecko_id,
        "holders_revenue_usd": round(usd, 2),
        "estimated_tokens": round(tokens, 4),
        "priced_days": priced,
        "revenue_days": len(usd_days),
        "supply_start": None if supply_start is None else round(supply_start, 4),
        "supply_end": None if supply_end is None else round(supply_end, 4),
        "supply_start_day": supply_days[0] if supply_days else None,
        "supply_end_day": supply_days[-1] if supply_days else None,
        "supply_change": None if growth is None else round(growth, 4),
        "supply_change_pct": None
        if growth is None or not supply_start
        else round(growth / supply_start * 100, 2),
        "buyback_vs_start_pct": None
        if not supply_start
        else round(tokens / supply_start * 100, 2),
        "exceeds_supply_growth": exceeds,
        "complete": complete,
    }


def main() -> None:
    load_env()
    today = datetime.now(timezone.utc).date()
    end = (today - timedelta(days=1)).isoformat()
    start = f"{today.year}-01-01"
    gecko = Gecko()
    rows: list[dict[str, Any]] = []
    for symbol, name, slug, gecko_id in WATCH:
        print(f"[{symbol}]")
        try:
            daily = holders_daily(slug)
            chart = gecko.chart(gecko_id)
        except Exception as exc:  # noqa: BLE001
            print(f"  [WARN] {symbol}: {exc}")
            rows.append({"symbol": symbol, "name": name, "error": str(exc), "exceeds_supply_growth": False, "complete": False})
            continue
        rows.append(build_row(symbol, name, slug, gecko_id, daily, chart, start, end))
    for symbol, name, reason in MISSING:
        rows.append(
            {
                "symbol": symbol,
                "name": name,
                "error": reason,
                "exceeds_supply_growth": False,
                "complete": False,
            }
        )
    payload = {
        "basis": "defillama_dailyHoldersRevenue_over_daily_close",
        "note": "持有人收入按当日收盘价折成代币，对照 CoinGecko 流通市值/价格。不是 Tokenomist 回购台账。",
        "window_start": start,
        "window_end": end,
        "updated_at": now_iso(),
        "rows": rows,
    }
    if not any(row.get("complete") for row in rows) and OUT_PATH.is_file():
        print("no complete rows; keep existing", OUT_PATH)
        return
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    hit = [row["symbol"] for row in rows if row.get("exceeds_supply_growth")]
    print(f"wrote {OUT_PATH} exceeds={','.join(hit) or '-'}")


if __name__ == "__main__":
    main()
