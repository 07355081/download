"""Step 1/3 — Download CoinGecko Crypto Treasuries data into local cache.

Next: 2.mark_holdings.py → 3.csv_to_json.py (or `python run_all.py`).

This script mirrors **the web-side fetch pattern** (TreasuriesPanel + lib/coingecko-treasuries.ts):
  - lazy: only what the page would request on first visit per coin
  - throttled: small batches with delay (DETAIL_ENRICH_BATCH_SIZE=4, _DELAY_MS=180)
  - cached: skip when local cache exists (acts like the page's in-memory cache)

Default ("--mode full") fetch set per coin:
  1) /entities/list (company + government, paged) -- shared across coins, fetched once
  2) /companies/public_treasury/<coin>  + /governments/public_treasury/<coin>  (paged)
  3) /public_treasury/<entity_id>       -- all entities in overview
  4) /public_treasury/<entity>/<coin>/holding_chart          -- dashboard windows (default 365,180,90,30)
     + /public_treasury/<entity>/transaction_history         -- all entities in overview

"--mode web" is still available for rate-limit-friendly lazy fetch:
it keeps top-N detail enrich and only default entity chart/transactions.

Outputs:
  - cache/entity_catalog.json
  - cache/overview_<coin_id>.json
  - cache/entity_<entity_id>.json
  - cache/entity_chart_<entity_id>_<coin_id>_<days>.json
  - cache/entity_transactions_<entity_id>_<coin_id>.json
  - output/holdings.csv
  - cache/download_summary.csv (internal log)

Auth:
  - Use --api-key or env COINGECKO_KEY (required for any reasonable throughput).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_URL = "https://api.coingecko.com/api/v3"
ENTITY_PAGE_SIZE = 250
MAX_ENTITY_PAGES = 20
MAX_TREASURY_PAGES = 10
# Match TreasuriesPanel entity JSON fallback (coingecko-treasuries.ts uses days=365).
# Demo API allows up to 365; "max" requires Analyst+ plan.
DEFAULT_CHART_DAYS = "365"
DASHBOARD_CHART_DAYS = ("365", "180", "90", "30")
SUPPORTED_COINS = ("bitcoin", "ethereum", "solana", "binancecoin", "ripple")

# Mirror lib/coingecko-treasuries.ts constants
DETAIL_ENRICH_TARGET_COUNT = 8
DETAIL_ENRICH_BATCH_SIZE = 4
DETAIL_ENRICH_BATCH_DELAY_MS = 180

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
CACHE_DIR = HERE / "cache"
OUTPUT_DIR = HERE / "output"
SUMMARY_CSV = CACHE_DIR / "download_summary.csv"

from holdings_csv import HOLDINGS_CSV, row_from_overview, write_rows_merged  # noqa: E402


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log(msg: str) -> None:
    print(msg, flush=True)


def normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def entity_lookup_keys(name: str, symbol: str | None, country: str | None) -> List[str]:
    n = normalize_text(name)
    s = normalize_text(symbol)
    c = normalize_text(country)
    keys = [f"{n}|{s}|{c}", f"{n}|{c}"]
    if s:
        keys.append(f"{s}|{c}")
    seen = set()
    out: List[str] = []
    for k in keys:
        if k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


def resolve_key(cli_key: str | None) -> str | None:
    if cli_key:
        return cli_key.strip() or None
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    env_key = (os.getenv("COINGECKO_KEY") or os.getenv("COINGECKO_DEMO_API_KEY") or "").strip()
    return env_key or None


def http_get_json(
    pathname: str,
    params: Dict[str, Any],
    api_key: str | None,
    retries: int,
    sleep_s: float,
) -> Any:
    url = f"{BASE_URL}{pathname}"
    qp = {k: v for k, v in params.items() if v is not None and v != ""}
    if qp:
        url = f"{url}?{urlencode(qp)}"

    headers = {"accept": "application/json"}
    if api_key:
        headers["x-cg-demo-api-key"] = api_key

    base_backoff = max(sleep_s, 0.5)
    last_err = ""
    for i in range(retries + 1):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=30) as r:
                raw = r.read().decode("utf-8")
            return json.loads(raw)
        except HTTPError as exc:
            last_err = f"HTTP {exc.code} {exc.reason}"
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if retryable and i < retries:
                # Honor Retry-After when provided; otherwise apply a key-aware backoff.
                retry_after_header = None
                try:
                    retry_after_header = exc.headers.get("Retry-After") if exc.headers else None
                except Exception:  # noqa: BLE001
                    retry_after_header = None
                if exc.code == 429:
                    if retry_after_header and str(retry_after_header).strip().isdigit():
                        wait = float(retry_after_header) + 1.0
                    elif api_key:
                        wait = min(15.0 * (2 ** i), 90.0)
                    else:
                        wait = min(60.0 * (2 ** i), 300.0)
                else:
                    wait = min(base_backoff * (2 ** i), 60.0)
                log(
                    f"    [warn] {pathname} -> {last_err}; backoff {wait:.1f}s ({i+1}/{retries})"
                )
                time.sleep(wait)
                continue
            raise RuntimeError(f"GET {url} failed: {last_err}")
        except (URLError, TimeoutError) as exc:
            last_err = str(exc)
            if i < retries:
                wait = min(base_backoff * (2 ** i), 60.0)
                log(f"    [warn] {pathname} -> {last_err}; backoff {wait:.1f}s ({i+1}/{retries})")
                time.sleep(wait)
                continue
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            if i < retries:
                time.sleep(base_backoff)
                continue
    raise RuntimeError(f"GET {url} failed after {retries + 1} attempts: {last_err}")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_all_entities(entity_type: str, api_key: str | None, retries: int, sleep_s: float) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for page in range(1, MAX_ENTITY_PAGES + 1):
        data = http_get_json(
            "/entities/list",
            {"entity_type": entity_type, "per_page": ENTITY_PAGE_SIZE, "page": page},
            api_key,
            retries,
            sleep_s,
        )
        time.sleep(sleep_s)
        if not isinstance(data, list):
            break
        out.extend(data)
        if len(data) < ENTITY_PAGE_SIZE:
            break
    return out


def build_entity_catalog(
    api_key: str | None, retries: int, sleep_s: float, force: bool
) -> Dict[str, Dict[str, str]]:
    catalog_path = CACHE_DIR / "entity_catalog.json"
    if catalog_path.exists() and not force:
        cached = read_json(catalog_path)
        cat = cached.get("catalog") if isinstance(cached, dict) else None
        if isinstance(cat, dict) and "company" in cat and "government" in cat:
            log(
                f"  catalog: reused cache company={len(cat['company'])} government={len(cat['government'])}"
            )
            return cat

    company_rows = fetch_all_entities("company", api_key, retries, sleep_s)
    government_rows = fetch_all_entities("government", api_key, retries, sleep_s)

    def rows_to_map(rows: Iterable[Dict[str, Any]]) -> Dict[str, str]:
        m: Dict[str, str] = {}
        for r in rows:
            entity_id = str(r.get("id") or "").strip()
            if not entity_id:
                continue
            for k in entity_lookup_keys(str(r.get("name") or ""), r.get("symbol"), r.get("country")):
                m.setdefault(k, entity_id)
        return m

    catalog = {
        "company": rows_to_map(company_rows),
        "government": rows_to_map(government_rows),
    }
    write_json(catalog_path, {"fetched_at": now_iso(), "catalog": catalog})
    return catalog


def fetch_collection(
    collection: str, coin_id: str, api_key: str | None, retries: int, sleep_s: float
) -> Tuple[float, float, float, List[Dict[str, Any]]]:
    rows: List[Dict[str, Any]] = []
    total_holdings = 0.0
    total_value = 0.0
    market_cap_dom = 0.0

    for page in range(1, MAX_TREASURY_PAGES + 1):
        payload = http_get_json(
            f"/{collection}/public_treasury/{coin_id}",
            {"per_page": ENTITY_PAGE_SIZE, "page": page, "order": "total_holdings_usd_desc"},
            api_key,
            retries,
            sleep_s,
        )
        time.sleep(sleep_s)
        if not isinstance(payload, dict):
            break
        if page == 1:
            total_holdings = float(payload.get("total_holdings") or 0.0)
            total_value = float(payload.get("total_value_usd") or 0.0)
            market_cap_dom = float(payload.get("market_cap_dominance") or 0.0)
        page_rows = payload.get(collection, [])
        if not isinstance(page_rows, list):
            page_rows = []
        rows.extend(page_rows)
        if len(page_rows) < ENTITY_PAGE_SIZE:
            break

    return total_holdings, total_value, market_cap_dom, rows


def to_overview_row(row: Dict[str, Any], entity_type: str, catalog: Dict[str, str]) -> Dict[str, Any]:
    entity_id = None
    for key in entity_lookup_keys(str(row.get("name") or ""), row.get("symbol"), row.get("country")):
        found = catalog.get(key)
        if found:
            entity_id = found
            break
    return {
        "name": row.get("name", ""),
        "symbol": row.get("symbol", ""),
        "country": row.get("country", ""),
        "total_holdings": float(row.get("total_holdings") or 0.0),
        "total_entry_value_usd": float(row.get("total_entry_value_usd") or 0.0),
        "total_current_value_usd": float(row.get("total_current_value_usd") or 0.0),
        "percentage_of_total_supply": float(row.get("percentage_of_total_supply") or 0.0),
        "entity_id": entity_id,
        "type": entity_type,
        "m_nav": None,
        "total_treasury_value_usd": None,
        "unrealized_pnl": None,
        "holding_amount_change_30d": None,
        "holding_change_percentage_30d": None,
        "website_url": None,
        "twitter_screen_name": None,
    }


def fetch_entity_detail(entity_id: str, api_key: str | None, retries: int, sleep_s: float) -> Dict[str, Any]:
    payload = http_get_json(
        f"/public_treasury/{entity_id}",
        {"holding_amount_change": "30d", "holding_change_percentage": "30d"},
        api_key,
        retries,
        sleep_s,
    )
    if not isinstance(payload, dict):
        raise RuntimeError(f"entity detail payload invalid: {entity_id}")
    return payload


def fetch_entity_chart(
    entity_id: str, coin_id: str, days: str, api_key: str | None, retries: int, sleep_s: float
) -> Dict[str, Any]:
    payload = http_get_json(
        f"/public_treasury/{entity_id}/{coin_id}/holding_chart",
        {"days": days, "include_empty_intervals": "true"},
        api_key,
        retries,
        sleep_s,
    )
    if not isinstance(payload, dict):
        raise RuntimeError(f"entity chart payload invalid: {entity_id} {coin_id} {days}")
    return payload


def fetch_entity_transactions(
    entity_id: str, coin_id: str, api_key: str | None, retries: int, sleep_s: float
) -> Dict[str, Any]:
    payload = http_get_json(
        f"/public_treasury/{entity_id}/transaction_history",
        {"per_page": 250, "page": 1, "order": "date_desc", "coin_ids": coin_id},
        api_key,
        retries,
        sleep_s,
    )
    if not isinstance(payload, dict):
        raise RuntimeError(f"entity transactions payload invalid: {entity_id} {coin_id}")
    return payload


def enrich_details_like_web(
    entity_ids: List[str],
    api_key: str | None,
    retries: int,
    sleep_s: float,
    batch_size: int,
    batch_delay_ms: int,
    force: bool,
) -> Dict[str, Dict[str, Any]]:
    """Mirror lib/coingecko-treasuries.ts enrichCompanyRowsWithDetail():
    - process at most DETAIL_ENRICH_TARGET_COUNT items (caller already slices)
    - in batches of DETAIL_ENRICH_BATCH_SIZE concurrently
    - sleep DETAIL_ENRICH_BATCH_DELAY_MS between batches
    - reuse cache when available
    """
    out: Dict[str, Dict[str, Any]] = {}
    pending: List[str] = []
    for eid in entity_ids:
        cache = CACHE_DIR / f"entity_{eid}.json"
        if cache.exists() and not force:
            try:
                out[eid] = read_json(cache)
                continue
            except Exception:  # noqa: BLE001
                pass
        pending.append(eid)

    if not pending:
        return out

    delay_s = max(batch_delay_ms / 1000.0, 0.0)
    for batch_start in range(0, len(pending), batch_size):
        batch = pending[batch_start : batch_start + batch_size]
        with ThreadPoolExecutor(max_workers=batch_size) as pool:
            futures = {
                pool.submit(fetch_entity_detail, eid, api_key, retries, sleep_s): eid for eid in batch
            }
            for fut in as_completed(futures):
                eid = futures[fut]
                try:
                    detail = fut.result()
                    out[eid] = detail
                    write_json(CACHE_DIR / f"entity_{eid}.json", detail)
                except Exception as exc:  # noqa: BLE001
                    log(f"    [warn] enrich detail failed {eid}: {exc}")
        if batch_start + batch_size < len(pending) and delay_s > 0:
            time.sleep(delay_s)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coin", default=None, help=f"Subset of {SUPPORTED_COINS}, comma-separated")
    parser.add_argument(
        "--mode",
        choices=("web", "full"),
        default="full",
        help="web: only fetch what the page would request on first visit per coin (lazy). "
        "full: enrich all entities; chart uses all --days windows (default 365,180,90,30).",
    )
    parser.add_argument(
        "--days",
        default=",".join(DASHBOARD_CHART_DAYS),
        help="Entity chart day windows (comma-separated), e.g. 365,180,90,30 or max. "
        "Default matches dashboard fallback: 365,180,90,30 (Demo API; max needs Analyst+).",
    )
    parser.add_argument(
        "--enrich-count",
        type=int,
        default=DETAIL_ENRICH_TARGET_COUNT,
        help=f"web mode: top-N companies to enrich detail for (default {DETAIL_ENRICH_TARGET_COUNT})",
    )
    parser.add_argument(
        "--enrich-batch-size",
        type=int,
        default=DETAIL_ENRICH_BATCH_SIZE,
        help=f"detail batch concurrency (default {DETAIL_ENRICH_BATCH_SIZE})",
    )
    parser.add_argument(
        "--enrich-batch-delay-ms",
        type=int,
        default=DETAIL_ENRICH_BATCH_DELAY_MS,
        help=f"sleep between detail batches in ms (default {DETAIL_ENRICH_BATCH_DELAY_MS})",
    )
    parser.add_argument("--coin-delay-s", type=float, default=1.0, help="sleep between coins")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--retry", type=int, default=6)
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.5,
        help="base sleep between requests; bumped to >=0.5 internally; demo key recommends >=1.5",
    )
    parser.add_argument("--force", action="store_true", help="Ignore existing cache and re-fetch all")
    args = parser.parse_args()

    api_key = resolve_key(args.api_key)
    if not api_key:
        log(
            "[warn] no COINGECKO_KEY provided; CoinGecko free tier is ~5-15 req/min. "
            "Forcing serial detail fetch + long sleep. Consider passing --api-key or "
            "setting $env:COINGECKO_KEY for ~30 req/min (demo key)."
        )
        if args.sleep < 6.0:
            args.sleep = 6.0
        if args.coin_delay_s < 5.0:
            args.coin_delay_s = 5.0
        if args.enrich_batch_size > 1:
            args.enrich_batch_size = 1
        if args.enrich_batch_delay_ms < 4000:
            args.enrich_batch_delay_ms = 4000
    coins = [c.strip() for c in args.coin.split(",")] if args.coin else list(SUPPORTED_COINS)
    coins = [c for c in coins if c in SUPPORTED_COINS]
    if not coins:
        log(f"[error] no valid coin in --coin; allowed: {SUPPORTED_COINS}")
        sys.exit(1)

    chart_day_list: List[str] = []
    for token in [d.strip() for d in args.days.split(",") if d.strip()]:
        if token not in chart_day_list:
            chart_day_list.append(token)
    if not chart_day_list:
        chart_day_list = list(DASHBOARD_CHART_DAYS)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    summary_rows: List[Dict[str, Any]] = []
    holdings_rows: List[Dict[str, str]] = []
    log(f"mode={args.mode} coins={coins} chart_days={chart_day_list} enrich_count={args.enrich_count}")

    log("building entity catalog...")
    catalog = build_entity_catalog(api_key, args.retry, args.sleep, args.force)
    log(
        f"entity catalog ready: company={len(catalog['company'])} government={len(catalog['government'])}"
    )

    for coin_index, coin_id in enumerate(coins):
        log(f"\n[coin {coin_index+1}/{len(coins)}] {coin_id}")
        started = time.time()
        try:
            c_holdings, c_value, c_dom, company_rows = fetch_collection(
                "companies", coin_id, api_key, args.retry, args.sleep
            )
            g_holdings, g_value, g_dom, government_rows = fetch_collection(
                "governments", coin_id, api_key, args.retry, args.sleep
            )
        except Exception as exc:  # noqa: BLE001
            summary_rows.append(
                {
                    "kind": "overview",
                    "coin_id": coin_id,
                    "entity_id": "",
                    "days": "",
                    "ok": False,
                    "rows": 0,
                    "elapsed_s": round(time.time() - started, 2),
                    "error": str(exc)[:500],
                }
            )
            log(f"  overview fetch failed: {exc}")
            continue

        companies = [to_overview_row(r, "company", catalog["company"]) for r in company_rows]
        governments = [to_overview_row(r, "government", catalog["government"]) for r in government_rows]

        # --- entity detail sync ---
        if args.mode == "full":
            candidates = sorted(
                {
                    r.get("entity_id")
                    for r in companies + governments
                    if r.get("entity_id")
                }
            )
            log(f"  enrich detail for all entities: {len(candidates)}")
        else:
            candidates = [r["entity_id"] for r in companies if r.get("entity_id")][: args.enrich_count]
            log(f"  enrich detail for top {len(candidates)} companies (web parity)")
        details_map = enrich_details_like_web(
            candidates,
            api_key,
            args.retry,
            args.sleep,
            args.enrich_batch_size,
            args.enrich_batch_delay_ms,
            args.force,
        )

        for r in companies:
            eid = r.get("entity_id")
            if not eid or eid not in details_map:
                continue
            detail = details_map[eid]
            holding = next(
                (h for h in detail.get("holdings", []) if h.get("coin_id") == coin_id),
                None,
            )
            r["m_nav"] = detail.get("m_nav")
            r["total_treasury_value_usd"] = detail.get("total_treasury_value_usd")
            r["unrealized_pnl"] = detail.get("unrealized_pnl")
            if holding:
                r["holding_amount_change_30d"] = (holding.get("holding_amount_change") or {}).get("30d")
                r["holding_change_percentage_30d"] = (holding.get("holding_change_percentage") or {}).get(
                    "30d"
                )
            r["website_url"] = detail.get("website_url") or None
            r["twitter_screen_name"] = detail.get("twitter_screen_name") or None

        ratio_leaders = sorted(
            [r for r in companies if r.get("m_nav") is not None],
            key=lambda x: float(x.get("total_current_value_usd") or 0.0),
            reverse=True,
        )
        m_nav_values = [r.get("m_nav") for r in ratio_leaders if r.get("m_nav") is not None]
        avg_mnav = (sum(m_nav_values) / len(m_nav_values)) if m_nav_values else None
        max_mnav = max(m_nav_values) if m_nav_values else None
        default_entity_id = next(
            (r.get("entity_id") for r in companies if r.get("entity_id")), None
        ) or next((r.get("entity_id") for r in governments if r.get("entity_id")), None)

        overview_payload = {
            "coin_id": coin_id,
            "summary": {
                "company_count": len(companies),
                "government_count": len(governments),
                "companies_total_holdings": c_holdings,
                "governments_total_holdings": g_holdings,
                "companies_total_value_usd": c_value,
                "governments_total_value_usd": g_value,
                "combined_total_value_usd": c_value + g_value,
                "combined_market_cap_dominance": c_dom + g_dom,
                "avg_company_m_nav": avg_mnav,
                "max_company_m_nav": max_mnav,
            },
            "companies": companies if args.mode == "full" else companies[:50],
            "governments": governments if args.mode == "full" else governments[:25],
            "ratio_leaders": ratio_leaders,
            "default_entity_id": default_entity_id,
            "fetched_at": now_iso(),
        }
        write_json(CACHE_DIR / f"overview_{coin_id}.json", overview_payload)

        for r in companies:
            holdings_rows.append(row_from_overview(coin_id, "company", r))
        for r in governments:
            holdings_rows.append(row_from_overview(coin_id, "government", r))

        summary_rows.append(
            {
                "kind": "overview",
                "coin_id": coin_id,
                "entity_id": "",
                "days": "",
                "ok": True,
                "rows": len(companies) + len(governments),
                "elapsed_s": round(time.time() - started, 2),
                "error": "",
            }
        )
        log(
            f"  overview saved: companies={len(companies)} governments={len(governments)} "
            f"default_entity_id={default_entity_id}"
        )

        # --- pick which entities to fetch chart + tx for ---
        if args.mode == "web":
            target_entities = [default_entity_id] if default_entity_id else []
        else:
            target_entities = sorted(
                {
                    r.get("entity_id")
                    for r in companies + governments
                    if r.get("entity_id")
                }
            )
        log(f"  chart/tx targets: {len(target_entities)} (mode={args.mode})")

        for idx, entity_id in enumerate(target_entities, 1):
            if not entity_id:
                continue

            # transactions for the coin
            tx_cache = CACHE_DIR / f"entity_transactions_{entity_id}_{coin_id}.json"
            tx_started = time.time()
            try:
                if tx_cache.exists() and not args.force:
                    tx_payload = read_json(tx_cache)
                else:
                    tx_payload = fetch_entity_transactions(
                        entity_id, coin_id, api_key, args.retry, args.sleep
                    )
                    write_json(tx_cache, tx_payload)
                    time.sleep(args.sleep)
                tx_rows = tx_payload.get("transactions", []) if isinstance(tx_payload, dict) else []
                summary_rows.append(
                    {
                        "kind": "entity_transactions",
                        "coin_id": coin_id,
                        "entity_id": entity_id,
                        "days": "",
                        "ok": True,
                        "rows": len(tx_rows) if isinstance(tx_rows, list) else 0,
                        "elapsed_s": round(time.time() - tx_started, 2),
                        "error": "",
                    }
                )
            except Exception as exc:  # noqa: BLE001
                summary_rows.append(
                    {
                        "kind": "entity_transactions",
                        "coin_id": coin_id,
                        "entity_id": entity_id,
                        "days": "",
                        "ok": False,
                        "rows": 0,
                        "elapsed_s": round(time.time() - tx_started, 2),
                        "error": str(exc)[:500],
                    }
                )

            for chart_days in chart_day_list:
                chart_cache = CACHE_DIR / f"entity_chart_{entity_id}_{coin_id}_{chart_days}.json"
                chart_started = time.time()
                try:
                    if chart_cache.exists() and not args.force:
                        chart = read_json(chart_cache)
                    else:
                        chart = fetch_entity_chart(
                            entity_id, coin_id, chart_days, api_key, args.retry, args.sleep
                        )
                        write_json(chart_cache, chart)
                        time.sleep(args.sleep)
                    points = chart.get("holdings", []) if isinstance(chart, dict) else []
                    summary_rows.append(
                        {
                            "kind": "entity_chart",
                            "coin_id": coin_id,
                            "entity_id": entity_id,
                            "days": chart_days,
                            "ok": True,
                            "rows": len(points) if isinstance(points, list) else 0,
                            "elapsed_s": round(time.time() - chart_started, 2),
                            "error": "",
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    summary_rows.append(
                        {
                            "kind": "entity_chart",
                            "coin_id": coin_id,
                            "entity_id": entity_id,
                            "days": chart_days,
                            "ok": False,
                            "rows": 0,
                            "elapsed_s": round(time.time() - chart_started, 2),
                            "error": str(exc)[:500],
                        }
                    )

            if idx % 10 == 0 or idx == len(target_entities):
                log(f"    progress {idx}/{len(target_entities)}")

        if coin_index < len(coins) - 1 and args.coin_delay_s > 0:
            time.sleep(args.coin_delay_s)

    total_rows, updated_rows = write_rows_merged(holdings_rows)
    log(
        f"holdings -> {HOLDINGS_CSV} (total={total_rows}, updated_this_run={updated_rows}; "
        "unchanged coins kept; run 2.mark_holdings.py to mark skip_json)"
    )

    fieldnames = ["kind", "coin_id", "entity_id", "days", "ok", "rows", "elapsed_s", "error"]
    with SUMMARY_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in summary_rows:
            w.writerow(row)

    fail_count = sum(1 for r in summary_rows if not r.get("ok"))
    log(f"\nDONE. rows={len(summary_rows)} failed={fail_count}")
    log(f"cache -> {CACHE_DIR}")
    log(f"download log -> {SUMMARY_CSV}")
    if fail_count:
        sys.exit(2)


if __name__ == "__main__":
    main()

