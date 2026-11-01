"""Monthly DEX and launchpad fees from DefiLlama.

The overview payload is about 5MB and has no daily history. This script keeps
the protocols in the Dexs and Launchpad categories that are large enough to
move a monthly total, fetches each daily fee chart, and writes only complete
UTC months. It does not call Dune and it does not keep the raw charts in the
published file.

Pons and Flap are Launchpad on DefiLlama, so a Dexs-only total cannot rank
them. Versions that share a parent protocol are summed (Uniswap V2/V3/V4,
Pons V1/V2). The published numbers are that sum, not a copied headline.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
JSON_DIR = HERE / "output" / "json"
CACHE_PATH = HERE / "cache" / "charts.json"
OUT_PATH = JSON_DIR / "dex_fees_monthly.json"

OVERVIEW_URL = (
    "https://api.llama.fi/overview/fees"
    "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyFees"
)
SUMMARY_URL = "https://api.llama.fi/summary/fees/{slug}?dataType=dailyFees"

CATEGORIES = frozenset({"Dexs", "Launchpad"})
MIN_ALL_TIME_USD = 10_000_000
MIN_30D_USD = 500_000
WORKERS = 6
ATTEMPTS = 3
TIMEOUT = 60
# A partial publish would make a month look quieter than it is.
MAX_FAILURES = 8
NAMES = {
    "parent#uniswap": "Uniswap",
    "parent#pons": "Pons",
    "parent#pump": "pump.fun",
    "parent#pancakeswap": "PancakeSwap",
    "parent#raydium": "Raydium",
    "parent#meteora": "Meteora",
    "parent#aerodrome": "Aerodrome",
    "parent#orca": "Orca",
    "parent#curve-finance": "Curve",
    "parent#balancer": "Balancer",
    "parent#trader-joe": "Trader Joe",
    "parent#hyperliquid": "Hyperliquid",
    "parent#bonkfun": "Bonk.fun",
    "slug:flap-sh": "Flap",
    "slug:stonkfun": "Stonk.fun",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get_json(url: str) -> tuple[int, dict | None]:
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "dashboard-data/dex-fees"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return 404, None
            last = exc
        except Exception as exc:  # noqa: BLE001
            last = exc
        if attempt < ATTEMPTS:
            time.sleep(min(2**attempt, 12))
    raise RuntimeError(f"GET {url} failed: {last}")


def group_key(protocol: dict) -> str:
    parent = str(protocol.get("parentProtocol") or "").strip()
    if parent:
        return parent
    return f"slug:{protocol.get('slug')}"


def group_name(key: str, protocol: dict | None) -> str:
    if key in NAMES:
        return NAMES[key]
    if key.startswith("parent#"):
        return key[len("parent#") :].replace("-", " ").title()
    if protocol:
        label = str(protocol.get("displayName") or protocol.get("name") or "").strip()
        if label:
            return label
    return key.removeprefix("slug:")


def qualifies(protocol: dict) -> bool:
    if protocol.get("category") not in CATEGORIES:
        return False
    slug = str(protocol.get("slug") or "").strip()
    if not slug:
        return False
    all_time = float(protocol.get("totalAllTime") or 0)
    recent = float(protocol.get("total30d") or 0)
    return all_time >= MIN_ALL_TIME_USD or recent >= MIN_30D_USD


def load_cache() -> dict[str, dict[str, float]]:
    if not CACHE_PATH.is_file():
        return {}
    try:
        raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    charts = raw.get("charts") if isinstance(raw, dict) else None
    if not isinstance(charts, dict):
        return {}
    out: dict[str, dict[str, float]] = {}
    for slug, series in charts.items():
        if isinstance(slug, str) and isinstance(series, dict):
            out[slug] = {str(day): float(value) for day, value in series.items() if isinstance(value, (int, float))}
    return out


def save_cache(charts: dict[str, dict[str, float]]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": now_iso(), "charts": charts}
    tmp = CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(CACHE_PATH)


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


def fetch_one(slug: str) -> tuple[str, dict[str, float] | None, str]:
    url = SUMMARY_URL.format(slug=urllib.parse.quote(slug, safe=".-"))
    try:
        status, payload = http_get_json(url)
    except Exception as exc:  # noqa: BLE001
        return slug, None, str(exc)
    if status == 404 or not payload:
        return slug, {}, "missing"
    return slug, chart_from_payload(payload), "ok"


def complete_month(month: str, today: str) -> bool:
    """Drop the UTC month that is still in progress. A finished month needs a late day."""
    if month >= today[:7]:
        return False
    return True


def build(charts: dict[str, dict[str, float]], names: dict[str, str], slugs: dict[str, str]) -> dict:
    today = datetime.now(timezone.utc).date().isoformat()
    # month -> name -> usd
    buckets: dict[str, dict[str, float]] = {}
    for slug, series in charts.items():
        key = slugs.get(slug)
        if not key:
            continue
        name = names.get(key) or key
        for day, value in series.items():
            month = day[:7]
            if not complete_month(month, today):
                continue
            # A month is usable once any day exists; the caller drops a thin trailing month below.
            buckets.setdefault(month, {})
            buckets[month][name] = buckets[month].get(name, 0.0) + float(value)

    months = sorted(buckets)
    if months:
        # DefiLlama sometimes lags the last day. Keep a prior month only when it
        # has more than a stub (the daily sum is already in the charts).
        last = months[-1]
        last_days = {
            day
            for series in charts.values()
            for day in series
            if day.startswith(last)
        }
        if len(last_days) < 20:
            months = months[:-1]

    rows = []
    for month in months:
        items = buckets[month]
        total = round(sum(items.values()), 2)
        protocols = [
            {"name": name, "fees_usd": round(value, 2)}
            for name, value in sorted(items.items(), key=lambda item: -item[1])
            if value >= 1000
        ]
        rows.append({"month": month, "fees_usd": total, "protocols": protocols})

    return {
        "code": "0",
        "msg": "success",
        "name": "dex_fees_monthly",
        "description": (
            "DefiLlama 分类 Dexs 与 Launchpad 的日手续费，按 parent 协议合并后的完整 UTC 月。"
            "Pons、Flap 在 DefiLlama 属于 Launchpad，所以合计包含发射台。"
            "数字是这条序列自己加总的。"
        ),
        "source": "defillama",
        "source_url": "https://defillama.com/fees",
        "universe": "Dexs+Launchpad",
        "updated_at": now_iso(),
        "protocol_slugs": len(slugs),
        "month_count": len(rows),
        "months": rows,
    }


def main() -> int:
    print("GET overview")
    status, overview = http_get_json(OVERVIEW_URL)
    if status != 200 or not overview:
        print("overview failed", status)
        return 1
    protocols = [p for p in overview.get("protocols") or [] if isinstance(p, dict) and qualifies(p)]
    print(f"selected {len(protocols)}")
    if len(protocols) < 20:
        print("too few protocols, refuse to publish")
        return 1

    slugs: dict[str, str] = {}
    names: dict[str, str] = {}
    for protocol in protocols:
        slug = str(protocol["slug"])
        key = group_key(protocol)
        slugs[slug] = key
        names[key] = group_name(key, protocol)

    cache = load_cache()
    fresh: dict[str, dict[str, float]] = {}
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(fetch_one, slug) for slug in slugs]
        done = 0
        for future in as_completed(futures):
            slug, chart, state = future.result()
            done += 1
            if chart is None:
                failures.append(f"{slug}: {state}")
            else:
                fresh[slug] = chart
            if done % 20 == 0 or done == len(slugs):
                print(f"  charts {done}/{len(slugs)} failures {len(failures)}")

    if len(failures) > MAX_FAILURES:
        print("too many chart failures, keep the previous file")
        for line in failures[:12]:
            print("   ", line)
        return 1

    merged = dict(cache)
    merged.update(fresh)
    # Drop slugs we no longer track so the cache cannot grow without a bound.
    merged = {slug: series for slug, series in merged.items() if slug in slugs}
    for slug in failures:
        name = slug.split(":", 1)[0]
        if name not in merged:
            print(f"    missing with no cache: {slug}")

    save_cache(merged)
    payload = build(merged, names, slugs)
    if payload["month_count"] < 13:
        print(f"only {payload['month_count']} complete months, refuse to publish")
        return 1

    JSON_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    latest = payload["months"][-1]
    top = ", ".join(f"{row['name']} {row['fees_usd'] / 1e8:.2f}亿" for row in latest["protocols"][:3])
    print(f"wrote {OUT_PATH} months={payload['month_count']} latest={latest['month']} {latest['fees_usd'] / 1e8:.2f}亿 top={top}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
