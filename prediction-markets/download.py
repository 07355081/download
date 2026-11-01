"""Prediction-market notional volume from DefiLlama DEX overview.

Pulls dailyVolume for protocols in category \"Prediction Market\", aggregates
complete UTC months and quarters, and writes a small JSON for the dashboard.

Does not call Dune. Does not scrape CryptoRank. Parent aggregates (e.g. slug
`polymarket` when children exist under parent#polymarket) are skipped to avoid
double counting.

Outputs:
  output/json/prediction_markets_quarterly.json
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
JSON_DIR = HERE / "output" / "json"
CACHE_PATH = HERE / "cache" / "charts.json"
OUT_PATH = JSON_DIR / "prediction_markets_quarterly.json"

OVERVIEW_URL = (
    "https://api.llama.fi/overview/dexs"
    "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyVolume"
)
SUMMARY_URL = "https://api.llama.fi/summary/dexs/{slug}?dataType=dailyVolume"

CATEGORY = "Prediction Market"
MIN_30D_USD = 5_000_000
MIN_24H_USD = 500_000
WORKERS = 6
ATTEMPTS = 3
TIMEOUT = 90
DISPLAY = {
    "kalshi": "Kalshi",
    "polymarket-us": "Polymarket US",
    "polymarket-international": "Polymarket International",
    "polymarket-combos": "Polymarket Combos",
    "predict-fun": "Predict Fun",
    "opinion": "OPINION",
    "hyperliquid-outcomes": "Hyperliquid Outcomes",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get_json(url: str) -> dict:
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "dashboard-data/prediction-markets"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < ATTEMPTS:
                time.sleep(min(2**attempt, 12))
    raise RuntimeError(f"GET {url} failed: {last}")


def display_name(slug: str, protocol: dict | None = None) -> str:
    if slug in DISPLAY:
        return DISPLAY[slug]
    if protocol:
        label = str(protocol.get("displayName") or protocol.get("name") or "").strip()
        if label:
            return label
    return slug.replace("-", " ").title()


def group_label(slug: str, parent: str | None, protocol: dict | None) -> str:
    """Roll Polymarket family into one brand for share sentences."""
    if parent == "parent#polymarket" or slug.startswith("polymarket"):
        return "Polymarket"
    if parent == "parent#sx-bet" or slug.startswith("sx-bet"):
        return "SX Bet"
    return display_name(slug, protocol)


def select_slugs(overview: dict) -> tuple[list[dict], set[str]]:
    protocols = [
        p
        for p in (overview.get("protocols") or [])
        if isinstance(p, dict) and str(p.get("category") or "") == CATEGORY and p.get("slug")
    ]
    parent_ids = {
        str(p.get("parentProtocol"))
        for p in protocols
        if p.get("parentProtocol")
    }
    skip_parent_slugs = {
        pid[len("parent#") :]
        for pid in parent_ids
        if isinstance(pid, str) and pid.startswith("parent#")
    }

    selected: list[dict] = []
    for p in protocols:
        slug = str(p["slug"])
        if slug in skip_parent_slugs:
            continue
        t30 = float(p.get("total30d") or 0)
        t24 = float(p.get("total24h") or 0)
        # Always keep majors even if quiet briefly.
        if slug in ("kalshi", "polymarket-us", "polymarket-international") or t30 >= MIN_30D_USD or t24 >= MIN_24H_USD:
            selected.append(p)
    return selected, skip_parent_slugs


def fetch_chart(slug: str) -> tuple[str, list[list[float]] | None, str | None]:
    try:
        summary = http_get_json(SUMMARY_URL.format(slug=slug))
        chart = summary.get("totalDataChart")
        if not isinstance(chart, list):
            return slug, None, "no chart"
        return slug, chart, None
    except Exception as exc:  # noqa: BLE001
        return slug, None, str(exc)[:160]


def day_from_ts(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def month_of(day: str) -> str:
    return day[:7]


def quarter_of(day: str) -> str:
    year = int(day[:4])
    month = int(day[5:7])
    q = (month - 1) // 3 + 1
    return f"{year}-Q{q}"


def complete_months(day_totals: dict[str, float]) -> set[str]:
    """UTC months that have at least 28 days of data (avoid partial current month)."""
    counts: dict[str, int] = defaultdict(int)
    for day in day_totals:
        counts[month_of(day)] += 1
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    current_month = today[:7]
    out: set[str] = set()
    for month, n in counts.items():
        if month >= current_month:
            continue
        if n >= 28:
            out.add(month)
    return out


def build_payload(
    selected: list[dict],
    charts: dict[str, list[list[float]]],
) -> dict:
    by_slug = {str(p["slug"]): p for p in selected}
    day_total: dict[str, float] = defaultdict(float)
    day_brand: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for slug, chart in charts.items():
        protocol = by_slug.get(slug)
        parent = str(protocol.get("parentProtocol") or "") if protocol else ""
        brand = group_label(slug, parent or None, protocol)
        for row in chart:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            day = day_from_ts(int(row[0]))
            amount = float(row[1] or 0)
            if amount <= 0:
                continue
            day_total[day] += amount
            day_brand[day][brand] += amount

    months_ok = complete_months(day_total)
    month_rows: list[dict] = []
    for month in sorted(months_ok):
        brands: dict[str, float] = defaultdict(float)
        total = 0.0
        for day, value in day_total.items():
            if month_of(day) != month:
                continue
            total += value
            for brand, amount in day_brand[day].items():
                brands[brand] += amount
        protocols = [
            {"name": name, "volume_usd": round(amount, 2)}
            for name, amount in sorted(brands.items(), key=lambda kv: -kv[1])
            if amount > 0
        ]
        month_rows.append({"month": month, "volume_usd": round(total, 2), "protocols": protocols})

    # Quarters: only when all 3 months are complete.
    by_month = {row["month"]: row for row in month_rows}
    quarter_rows: list[dict] = []
    years = sorted({int(m[:4]) for m in by_month})
    for year in years:
        for q in (1, 2, 3, 4):
            keys = [f"{year}-{str(m).zfill(2)}" for m in ((q - 1) * 3 + 1, (q - 1) * 3 + 2, (q - 1) * 3 + 3)]
            if any(k not in by_month for k in keys):
                continue
            brands: dict[str, float] = defaultdict(float)
            total = 0.0
            for key in keys:
                row = by_month[key]
                total += float(row["volume_usd"])
                for protocol in row["protocols"]:
                    brands[str(protocol["name"])] += float(protocol["volume_usd"])
            protocols = [
                {"name": name, "volume_usd": round(amount, 2)}
                for name, amount in sorted(brands.items(), key=lambda kv: -kv[1])
            ]
            quarter_rows.append(
                {
                    "quarter": f"{year}-Q{q}",
                    "volume_usd": round(total, 2),
                    "months": keys,
                    "protocols": protocols,
                }
            )

    return {
        "code": "0",
        "msg": "success",
        "name": "prediction_markets_quarterly",
        "description": (
            "DefiLlama DEX overview 中 category=Prediction Market 的日名义成交，"
            "按品牌合并后汇总完整 UTC 月/季。跳过父级汇总 slug，避免与子协议双计。"
        ),
        "source": "defillama",
        "source_url": "https://defillama.com/protocols/Prediction-Market",
        "metric": "dailyVolume",
        "note": (
            "名义成交口径，不等于用户实付本金；与 CryptoRank 等第三方全行业统计可能相差较大，"
            "因覆盖交易所与计量方式不同。无体育等类别拆分。"
        ),
        "generated_at": now_iso(),
        "protocol_count": len(charts),
        "month_count": len(month_rows),
        "quarter_count": len(quarter_rows),
        "months": month_rows,
        "quarters": quarter_rows,
        "data": quarter_rows,
    }


def main() -> int:
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

    overview = http_get_json(OVERVIEW_URL)
    selected, skipped = select_slugs(overview)
    print(f"selected={len(selected)} skip_parent_slugs={sorted(skipped)}")

    charts: dict[str, list[list[float]]] = {}
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = [pool.submit(fetch_chart, str(p["slug"])) for p in selected]
        for fut in as_completed(futs):
            slug, chart, err = fut.result()
            if chart is None:
                failures.append(f"{slug}: {err}")
                print("FAIL", slug, err)
                continue
            charts[slug] = chart
            print("OK", slug, "points", len(chart))

    if len(charts) < 3:
        raise SystemExit(f"too few charts ({len(charts)}); failures={failures[:5]}")

    CACHE_PATH.write_text(
        json.dumps({"generated_at": now_iso(), "slugs": sorted(charts), "failures": failures}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    payload = build_payload(selected, charts)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    latest = (payload.get("quarters") or [])[-1] if payload.get("quarters") else None
    if latest:
        top = ", ".join(
            f"{row['name']} {row['volume_usd'] / 1e8:.2f}亿" for row in latest["protocols"][:3]
        )
        print(
            f"wrote {OUT_PATH} quarters={payload['quarter_count']} "
            f"latest={latest['quarter']} {latest['volume_usd'] / 1e8:.2f}亿 top={top}"
        )
    else:
        print(f"wrote {OUT_PATH} but no complete quarters yet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
