"""Download Glassnode metrics via Alphanode -> cache/glassnode/ + output/csv/glassnode/indicators.csv

Built-in catalog (glassnode_metrics_catalog.py):
  - BTC 17 项: 矿工/筹码/持币成本/周期估值（精选）
  - ETH 12 项: 收入/使用/质押/核心估值（精选）

Usage:
    python download_glassnode.py              # fetch missing + stale cache (default)
    python download_glassnode.py --cache-only # merge existing cache only (no API)
    python download_glassnode.py --no-skip-cached  # refresh all metrics even if fresh
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib import error, parse, request

from download_coinglass import write_merged_csv
from glassnode_metrics_catalog import METRIC_SPECS

BASE_URL = "https://api.alphanode.work"
USER_AGENT = "btc-index-glassnode-fetch/1.0"

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_GLASS_CACHE = SCRIPT_DIR / "cache" / "glassnode"
DEFAULT_OUT = SCRIPT_DIR / "output" / "csv" / "glassnode" / "indicators.csv"
DEFAULT_STATUS = SCRIPT_DIR / "output" / "csv" / "glassnode" / "_run_status.csv"

DAY_S = 24 * 60 * 60
GLASS_CACHE_HEADER = ["time_unix", "time_utc", "value"]

# Hard-coded key (lowest priority; env / .env / --api-key override)
API_KEY = "myapi_sk_671c771c1c4aa3a1925655f7641d0a8b"
DEFAULT_RECHECK_DAYS = 2
DEFAULT_MAX_REQUESTS = 100
# Alphanode quota: these HTTP statuses do not consume a billable download.
QUOTA_EXEMPT_HTTP = frozenset({403, 404, 429})
PRIORITY_METRIC = "Options Max Pain (Time Series)"


@dataclass
class MetricTask:
    rank: int
    name: str
    path: str
    params: dict[str, str]
    risk: str


def defs_dir() -> Path:
    local = SCRIPT_DIR / "glassnode_defs"
    if (local / "api筛选.md").exists() and (local / "api总览.md").exists():
        return local
    sibling = SCRIPT_DIR.parent.parent / "glassnode"
    if (sibling / "api筛选.md").exists() and (sibling / "api总览.md").exists():
        return sibling
    raise FileNotFoundError(
        "Glassnode 定义文件未找到。请将 api筛选.md 与 api总览.md 放入 "
        f"{SCRIPT_DIR / 'glassnode_defs'}，或保留同级的 C:\\code\\glassnode 目录。"
    )


def load_env(p: Path) -> None:
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def cache_latest_is_fresh(existing_max_t: int | None, recheck_days: int) -> bool:
    """True when cache latest point is within recheck_days of now (UTC)."""
    if existing_max_t is None:
        return False
    cutoff = int(datetime.now(tz=timezone.utc).timestamp()) - recheck_days * DAY_S
    return existing_max_t >= cutoff


def counts_toward_quota(status: int) -> bool:
    return status not in QUOTA_EXEMPT_HTTP


def resolve_api_key(arg_key: str | None) -> str:
    if arg_key:
        return arg_key
    load_env(SCRIPT_DIR / ".env")
    key = os.environ.get("ALPHANODE_API_KEY") or API_KEY
    if not key:
        sys.exit("ERROR: ALPHANODE_API_KEY not provided (use --api-key, env, .env, or hard-code).")
    return key


def parse_selected_metrics(p: Path) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for line in p.read_text(encoding="utf-8").splitlines()[1:]:
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        try:
            rank = int(parts[0].strip())
        except ValueError:
            continue
        name = parts[1].strip()
        if name:
            out.append((rank, name))
    return out


def extract_block_for_metric(overview_text: str, name: str) -> str | None:
    start = overview_text.find(f"## {name}")
    if start == -1:
        return None
    next_start = overview_text.find("\n## ", start + 4)
    return overview_text[start : next_start if next_start != -1 else len(overview_text)]


def choose_param_value(name: str, enum_values: list[str]) -> str | None:
    if name == "a":
        return "BTC"
    if name == "i":
        return "24h" if "24h" in enum_values else (enum_values[0] if enum_values else "24h")
    if name == "maturity":
        return "aggregated" if "aggregated" in enum_values else (enum_values[0] if enum_values else None)
    if name == "c":
        return "USD" if "USD" in enum_values else (enum_values[0] if enum_values else None)
    if name == "period":
        return "1y" if "1y" in enum_values else (enum_values[0] if enum_values else None)
    if name in {"f", "timestamp_format", "s", "u"}:
        return None
    return enum_values[0] if enum_values else None


def parse_path_and_params(block: str) -> tuple[str | None, dict[str, str]]:
    path_match = re.search(r'"/v1/metrics/[^"]+"', block)
    if not path_match:
        return None, {}
    path = path_match.group(0).strip('"')

    params: dict[str, str] = {}
    req_pat = re.compile(
        r'"name":"([^"]+)","in":"query","required":true.*?"schema":\{([^}]*)\}',
        re.DOTALL,
    )
    for m in req_pat.finditer(block):
        pname = m.group(1)
        schema = m.group(2)
        enum_match = re.search(r'"enum":\[(.*?)\]', schema)
        enum_values = [x.strip().strip('"') for x in enum_match.group(1).split(",")] if enum_match else []
        v = choose_param_value(pname, enum_values)
        if v is not None:
            params[pname] = v
    if "a" not in params:
        params["a"] = "BTC"
    return path, params


def classify_risk(path: str, params: dict[str, str]) -> str:
    if (
        "/options/" in path
        or "/derivatives/" in path
        or any(k in params for k in ("maturity", "e", "period"))
        or params.get("i", "24h") != "24h"
    ):
        return "high"
    return "low"


def build_tasks(metrics: list[tuple[int, str]], overview_text: str) -> tuple[list[MetricTask], list[str]]:
    tasks: list[MetricTask] = []
    missing: list[str] = []
    for rank, name in metrics:
        block = extract_block_for_metric(overview_text, name)
        if not block:
            missing.append(name)
            continue
        path, params = parse_path_and_params(block)
        if not path:
            missing.append(name)
            continue
        tasks.append(MetricTask(rank, name, path, params, classify_risk(path, params)))
    return tasks, missing


def reorder_tasks(tasks: list[MetricTask]) -> list[MetricTask]:
    low = sorted([t for t in tasks if t.risk == "low"], key=lambda x: x.rank)
    high = sorted([t for t in tasks if t.risk == "high"], key=lambda x: x.rank)
    high.sort(key=lambda x: (0 if x.name == PRIORITY_METRIC else 1, x.rank))
    return low + high


def build_catalog_tasks() -> list[MetricTask]:
    """Convert built-in METRIC_SPECS to download tasks."""
    tasks: list[MetricTask] = []
    for spec in METRIC_SPECS:
        params = dict(spec.params or {"a": spec.asset, "i": "24h"})
        tasks.append(
            MetricTask(
                rank=spec.rank,
                name=spec.name,
                path=spec.path,
                params=params,
                risk=classify_risk(spec.path, params),
            )
        )
    return tasks


def resolve_tasks_from_defs() -> tuple[list[MetricTask], list[str], Path]:
    ddir = defs_dir()
    ranking = ddir / "api筛选.md"
    overview = ddir / "api总览.md"
    metrics = parse_selected_metrics(ranking)
    overview_text = overview.read_text(encoding="utf-8")
    tasks, missing = build_tasks(metrics, overview_text)
    return reorder_tasks(tasks), missing, ddir


def resolve_tasks() -> tuple[list[MetricTask], list[str], str]:
    """Prefer legacy defs; fall back to built-in catalog from docs.glassnode.com."""
    try:
        tasks, missing, ddir = resolve_tasks_from_defs()
        if tasks:
            return tasks, missing, str(ddir)
    except FileNotFoundError:
        pass
    tasks = build_catalog_tasks()
    return reorder_tasks(tasks), [], "builtin catalog"


def http_get_json(
    url: str, params: dict[str, str], headers: dict[str, str], retries: int = 3, sleep: float = 1.5
) -> tuple[int, Any]:
    last_err: Exception | None = None
    full_url = url + "?" + parse.urlencode(params)
    for attempt in range(1, retries + 1):
        req = request.Request(full_url, headers={**headers, "User-Agent": USER_AGENT, "accept": "application/json"})
        try:
            with request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8")
                return resp.getcode(), (json.loads(body) if body.strip() else None)
        except error.HTTPError as e:
            try:
                body_text = e.read().decode("utf-8", errors="replace")
            except Exception:
                body_text = ""
            return e.code, body_text
        except (error.URLError, json.JSONDecodeError, TimeoutError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(sleep * attempt)
    raise RuntimeError(f"GET {full_url} failed after {retries} attempts: {last_err}")


def safe_filename(rank: int, name: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
    return f"{rank:03d}_{safe}.csv"


def metric_cache_path(task: MetricTask, cache_dir: Path) -> Path:
    return cache_dir / safe_filename(task.rank, task.name)


def read_glassnode_cache(p: Path) -> dict[int, str]:
    if not p.exists():
        return {}
    out: dict[int, str] = {}
    try:
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header or header[0] != "time_unix":
                return {}
            for row in reader:
                if len(row) < 3:
                    continue
                try:
                    t = int(row[0])
                except ValueError:
                    continue
                out[t] = row[2]
    except OSError:
        return {}
    return out


def write_glassnode_cache(p: Path, series: dict[int, str]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(GLASS_CACHE_HEADER)
        for t in sorted(series.keys()):
            dt = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            writer.writerow([t, dt, series[t]])


def normalize_value(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (int, float, str)):
        return str(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def extract_series(payload: Any) -> dict[int, str]:
    out: dict[int, str] = {}
    if not isinstance(payload, list):
        return out
    for item in payload:
        if not isinstance(item, dict):
            continue
        t = item.get("t")
        if not isinstance(t, int):
            continue
        v = item.get("v")
        if v is None and "o" in item:
            v = item.get("o")
        out[t] = normalize_value(v)
    return out


def endpoint_id_for_metric(rank: int, name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9\-]+", "-", name).strip("-")
    s = re.sub(r"-+", "-", s)
    if not s:
        s = "metric"
    return f"glassnode-{rank:03d}-{s}"[:160]


def ts_to_date_utc(ts_ms: int) -> str:
    try:
        return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return ""


def metric_series_to_indicator_rows(endpoint_id: str, series: dict[int, str]) -> list[list[str]]:
    """Long rows compatible with download.py / csv_to_json.py."""
    rows: list[list[str]] = []
    for t_unix in sorted(series.keys()):
        ts_ms = t_unix * 1000
        rows.append(
            [
                endpoint_id,
                "series",
                str(ts_ms),
                ts_to_date_utc(ts_ms),
                "",
                "value",
                series[t_unix],
            ]
        )
    return rows


def write_status(out_path: Path, rows: list[dict[str, Any]]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    header = [
        "index",
        "rank",
        "name",
        "path",
        "params",
        "risk",
        "status_code",
        "ok",
        "rows_added",
        "cache_total",
        "error_body",
    ]
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for r in rows:
            writer.writerow(
                [
                    r.get("index", ""),
                    r.get("rank", ""),
                    r.get("name", ""),
                    r.get("path", ""),
                    normalize_value(r.get("params", {})),
                    r.get("risk", ""),
                    r.get("status_code", ""),
                    r.get("ok", ""),
                    r.get("rows_added", ""),
                    r.get("cache_total", ""),
                    r.get("error_body", ""),
                ]
            )


def fetch_glassnode_tasks(
    *,
    cache_dir: Path,
    status_path: Path,
    recheck_days: int,
    api_key: str,
    retry: int,
    sleep_s: float,
    force: bool,
    max_failures: int,
    max_requests: int,
    skip_cached: bool,
) -> list[MetricTask]:
    """Download / update caches. Returns ordered task list (same order as caches merged)."""
    tasks, missing, source = resolve_tasks()
    print(f"[glassnode] source={source}  resolved={len(tasks)}  missing={len(missing)}  cache={cache_dir}")
    print(
        f"[glassnode] quota: max_requests={max_requests}  skip_fresh={skip_cached}  "
        f"recheck_days={recheck_days}  exempt_http={sorted(QUOTA_EXEMPT_HTTP)}",
        flush=True,
    )
    if skip_cached and not force:
        to_fetch = 0
        for t in tasks:
            series = read_glassnode_cache(metric_cache_path(t, cache_dir))
            latest = max(series.keys()) if series else None
            if not cache_latest_is_fresh(latest, recheck_days):
                to_fetch += 1
        print(f"[glassnode] plan: fetch {to_fetch}/{len(tasks)} (missing or stale cache)", flush=True)
    if missing:
        head = ", ".join(missing[:8]) + ("..." if len(missing) > 8 else "")
        print(f"[glassnode] MISSING (no path in overview): {head}")

    cache_dir.mkdir(parents=True, exist_ok=True)
    headers = {"x-key": api_key}
    status_rows: list[dict[str, Any]] = []
    failures = 0
    billable_requests = 0

    for idx, task in enumerate(tasks, 1):
        cache_file = metric_cache_path(task, cache_dir)
        cached = {} if force else read_glassnode_cache(cache_file)
        existing_max_t = max(cached.keys()) if cached else None
        params = dict(task.params)

        if skip_cached and not force and cache_latest_is_fresh(existing_max_t, recheck_days):
            latest = datetime.fromtimestamp(existing_max_t, tz=timezone.utc).strftime("%Y-%m-%d")
            print(
                f"[glassnode {idx:03d}/{len(tasks)}] {task.name}  "
                f"(skip: fresh, latest {latest}, {len(cached)} rows)",
                flush=True,
            )
            continue

        if billable_requests >= max_requests:
            print(f"\n[glassnode] STOPPED: billable_requests={billable_requests} >= limit={max_requests}", flush=True)
            break

        if existing_max_t is not None:
            since = max(0, existing_max_t - recheck_days * DAY_S)
            params["s"] = str(since)
            mode = f"incremental from {datetime.fromtimestamp(since, tz=timezone.utc).strftime('%Y-%m-%d')}"
        else:
            mode = "full history"

        print(f"[glassnode {idx:03d}/{len(tasks)}] {task.name}  ({mode})", flush=True)

        try:
            status, payload = http_get_json(BASE_URL + task.path, params, headers, retries=retry, sleep=1.5)
        except Exception as e:
            billable_requests += 1
            failures += 1
            status_rows.append(
                {
                    "index": idx,
                    "rank": task.rank,
                    "name": task.name,
                    "path": task.path,
                    "params": params,
                    "risk": task.risk,
                    "status_code": -1,
                    "ok": False,
                    "error_body": str(e)[:800],
                    "cache_total": len(cached),
                }
            )
            print(f"  EXCEPTION: {e}  (billable {billable_requests}/{max_requests})")
            write_status(status_path, status_rows)
            if failures > max_failures:
                print(f"\n[glassnode] STOPPED: failures={failures} > limit={max_failures}")
                break
            time.sleep(sleep_s)
            continue

        if counts_toward_quota(status):
            billable_requests += 1

        ok = status == 200
        if not ok:
            if counts_toward_quota(status):
                failures += 1
            exempt = "" if counts_toward_quota(status) else " (quota-exempt)"
            err_text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
            status_rows.append(
                {
                    "index": idx,
                    "rank": task.rank,
                    "name": task.name,
                    "path": task.path,
                    "params": params,
                    "risk": task.risk,
                    "status_code": status,
                    "ok": False,
                    "error_body": (err_text or "")[:800],
                    "cache_total": len(cached),
                }
            )
            print(f"  FAIL status={status}{exempt}: {(err_text or '')[:120]}  (billable {billable_requests}/{max_requests})")
            write_status(status_path, status_rows)
            if failures > max_failures:
                print(f"\n[glassnode] STOPPED: failures={failures} > limit={max_failures}")
                break
            time.sleep(sleep_s)
            continue

        new_series = extract_series(payload)
        merged_series = {**cached, **new_series}
        added = sum(1 for t in new_series if existing_max_t is None or t > existing_max_t)
        write_glassnode_cache(cache_file, merged_series)
        new_max = max(merged_series.keys()) if merged_series else None
        max_label = datetime.fromtimestamp(new_max, tz=timezone.utc).strftime("%Y-%m-%d") if new_max else "(empty)"
        if added == 0 and existing_max_t is not None:
            print(f"  no new days (refreshed window, latest {max_label})  (billable {billable_requests}/{max_requests})")
        else:
            print(f"  +{added} new rows  (cache total: {len(merged_series)}, latest {max_label})  (billable {billable_requests}/{max_requests})")

        status_rows.append(
            {
                "index": idx,
                "rank": task.rank,
                "name": task.name,
                "path": task.path,
                "params": params,
                "risk": task.risk,
                "status_code": status,
                "ok": True,
                "rows_added": added,
                "cache_total": len(merged_series),
            }
        )
        write_status(status_path, status_rows)
        time.sleep(sleep_s)

    write_status(status_path, status_rows)
    return tasks


def _tasks_from_cache(cache_dir: Path) -> list[MetricTask]:
    """Build task list from existing cache filenames (no defs required)."""
    tasks: list[MetricTask] = []
    for p in sorted(cache_dir.glob("*.csv")):
        m = re.match(r"^(\d{3})_(.+)\.csv$", p.name)
        if not m:
            continue
        rank = int(m.group(1))
        name = m.group(2)
        tasks.append(MetricTask(rank, name, "", {}, "low"))
    return sorted(tasks, key=lambda t: t.rank)


def all_glassnode_indicator_rows(cache_dir: Path, tasks: Iterable[MetricTask]) -> list[list[str]]:
    rows: list[list[str]] = []
    for task in tasks:
        eid = endpoint_id_for_metric(task.rank, task.name)
        series = read_glassnode_cache(metric_cache_path(task, cache_dir))
        rows.extend(metric_series_to_indicator_rows(eid, series))
    return rows


def run_fetch(
    *,
    cache_dir: Path | None = None,
    status_path: Path | None = None,
    recheck_days: int = DEFAULT_RECHECK_DAYS,
    api_key: str | None = None,
    retry: int = 3,
    sleep_s: float = 0.35,
    force: bool = False,
    max_failures: int = 6,
    max_requests: int = DEFAULT_MAX_REQUESTS,
    skip_cached: bool = True,
    cache_only: bool = False,
) -> list[list[str]]:
    """Fetch Glassnode metrics into cache, return long-format rows for indicators.csv."""
    cdir = cache_dir or DEFAULT_GLASS_CACHE
    spath = status_path or DEFAULT_STATUS

    if cache_only:
        tasks = build_catalog_tasks()
        cached_n = sum(1 for t in tasks if metric_cache_path(t, cdir).is_file())
        print(f"[glassnode] cache-only: {cached_n}/{len(tasks)} catalog metrics cached under {cdir}")
        return all_glassnode_indicator_rows(cdir, tasks)

    key = resolve_api_key(api_key)
    tasks = fetch_glassnode_tasks(
        cache_dir=cdir,
        status_path=spath,
        recheck_days=recheck_days,
        api_key=key,
        retry=retry,
        sleep_s=sleep_s,
        force=force,
        max_failures=max_failures,
        max_requests=max_requests,
        skip_cached=skip_cached,
    )
    return all_glassnode_indicator_rows(cdir, tasks)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Merged long CSV path")
    parser.add_argument("--cache-dir", default=str(DEFAULT_GLASS_CACHE), help="Per-metric cache directory")
    parser.add_argument("--status", default=str(DEFAULT_STATUS), help="Status report CSV")
    parser.add_argument("--force", action="store_true", help="Re-download every metric from scratch")
    parser.add_argument("--recheck-days", type=int, default=DEFAULT_RECHECK_DAYS)
    parser.add_argument("--api-key", default=None, help="Alphanode x-key")
    parser.add_argument("--retry", type=int, default=3)
    parser.add_argument("--sleep", type=float, default=0.35, help="Seconds between requests")
    parser.add_argument("--max-failures", type=int, default=6)
    parser.add_argument(
        "--max-requests",
        type=int,
        default=DEFAULT_MAX_REQUESTS,
        help="Stop after this many billable API calls (403/404/429 are exempt)",
    )
    parser.add_argument(
        "--skip-cached",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip metrics whose cache is already fresh within --recheck-days (default: true)",
    )
    parser.add_argument("--no-merge", action="store_true", help="Skip writing merged indicators.csv")
    parser.add_argument("--cache-only", action="store_true", help="Merge existing cache only; no defs/API")
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir).resolve()
    status_path = Path(args.status).resolve()
    out_path = Path(args.out).resolve()
    rows = run_fetch(
        cache_dir=cache_dir,
        status_path=status_path,
        recheck_days=args.recheck_days,
        api_key=args.api_key,
        retry=args.retry,
        sleep_s=args.sleep,
        force=args.force,
        max_failures=args.max_failures,
        max_requests=args.max_requests,
        skip_cached=args.skip_cached,
        cache_only=args.cache_only,
    )
    print(f"\n[glassnode] long-format rows: {len(rows):,}")
    if not args.no_merge and rows:
        total = write_merged_csv(out_path, rows)
        print(f"[glassnode] merged {total:,} rows -> {out_path}")
    print(f"[glassnode] status -> {status_path}")


if __name__ == "__main__":
    main()

