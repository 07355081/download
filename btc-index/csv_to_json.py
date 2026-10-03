"""Convert indicators CSV -> per-endpoint JSON (Coinglass + Glassnode).

Reads:
  output/csv/coinglass/indicators.csv  -> output/json/coinglass/
  output/csv/glassnode/indicators.csv  -> output/json/glassnode/  (if present)

Also writes canonical BTC spot daily from NUPL embedded prices:
  output/json/coinglass/btc_spot_daily.json

Layout (no *.json directly under output/json/):
  output/json/coinglass/*.json
  output/json/glassnode/{rank:03d}_{slug}.json

Usage:
    python csv_to_json.py
    python csv_to_json.py --skip-glassnode
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

UTC = timezone.utc
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DOWNLOAD_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(DATA_DOWNLOAD_ROOT) not in sys.path:
    sys.path.insert(0, str(DATA_DOWNLOAD_ROOT))
import _json_merge as JM  # noqa: E402
from glassnode_metrics_catalog import METRIC_SPECS  # noqa: E402
DEFAULT_COINGLASS_IN = SCRIPT_DIR / "output" / "csv" / "coinglass" / "indicators.csv"
DEFAULT_GLASSNODE_IN = SCRIPT_DIR / "output" / "csv" / "glassnode" / "indicators.csv"
DEFAULT_COINGLASS_OUT = SCRIPT_DIR / "output" / "json" / "coinglass"
DEFAULT_GLASSNODE_OUT = SCRIPT_DIR / "output" / "json" / "glassnode"
DEFAULT_JSON_ROOT = SCRIPT_DIR / "output" / "json"
DEFAULT_BTC_SPOT_DAILY_OUT = DEFAULT_JSON_ROOT / "coinglass" / "btc_spot_daily.json"

GLASSNODE_EP_RE = re.compile(r"^glassnode-(\d+)-(.+)$", re.I)

NUPL_ENDPOINT = "bitcoin-net-unrealized-profit-loss"

COINGLASS_ENDPOINT_OUT: dict[str, str] = {
    "ahr999": "ahr999.json",
    "puell-multiple": "puell-multiple.json",
    "fear-greed-history": "fear-greed-history.json",
    "200-week-moving-average-heatmap": "200-week-moving-average-heatmap.json",
    "bull-market-peak-indicator": "bull-market-peak-indicator.json",
    "bitcoin-sth-sopr": "bitcoin-sth-sopr.json",
    "bitcoin-lth-sopr": "bitcoin-lth-sopr.json",
    "bitcoin-sth-realized-price": "bitcoin-sth-realized-price.json",
    "bitcoin-lth-realized-price": "bitcoin-lth-realized-price.json",
    "bitcoin-short-term-holder-supply": "bitcoin-short-term-holder-supply.json",
    "bitcoin-long-term-holder-supply": "bitcoin-long-term-holder-supply.json",
    "bitcoin-rhodl-ratio": "bitcoin-rhodl-ratio.json",
    "bitcoin-net-unrealized-profit-loss": "bitcoin-net-unrealized-profit-loss.json",
    "bitcoin-macro-oscillator": "bitcoin-macro-oscillator.json",
    "bitcoin-profitable-days": "bitcoin-profitable-days.json",
}


def maybe_num(v: str) -> Any:
    if v == "":
        return None
    try:
        n = float(v)
    except ValueError:
        return v
    if n != n or n in (float("inf"), float("-inf")):
        return None
    if n.is_integer() and abs(n) < 1e15:
        return int(n)
    return n


def load_csv_dicts(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_json(out_root: Path, rel: str, data: Any, *, merge_fn) -> None:
    p = out_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"code": "0", "msg": "success", "data": data}
    JM.write_dashboard_json(p, payload, merge_data_fn=merge_fn)
    print(f"  wrote {rel}  ({p.stat().st_size:,} bytes)")


def build_series_payload(rows: list[dict[str, str]]) -> dict[str, Any]:
    by_ts: dict[int, dict[str, Any]] = defaultdict(dict)
    for r in rows:
        try:
            ts = int(r["timestamp_ms"])
        except (KeyError, ValueError):
            continue
        field = r["field"]
        val = maybe_num(r["value"])
        if val is None:
            continue
        if field == "price":
            by_ts[ts]["__price__"] = val
        else:
            by_ts[ts][field] = val

    if not by_ts:
        return {"data_list": [], "time_list": []}

    sorted_ts = sorted(by_ts.keys())
    time_list: list[int] = []
    data_list: list[Any] = []
    price_list: list[Any] = []
    has_price = False
    for ts in sorted_ts:
        bag = by_ts[ts]
        price = bag.pop("__price__", None)
        time_list.append(ts)
        if not bag:
            data_list.append(None)
        elif list(bag.keys()) == ["value"]:
            data_list.append(bag["value"])
        else:
            data_list.append(dict(bag))
        if price is not None:
            has_price = True
        price_list.append(price)

    payload: dict[str, Any] = {"data_list": data_list, "time_list": time_list}
    if has_price:
        payload["price_list"] = price_list
    return payload


def build_snapshot_payload(rows: list[dict[str, str]]) -> Any:
    by_group: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for r in rows:
        g = r.get("group", "")
        if g not in by_group:
            by_group[g] = {}
            order.append(g)
        by_group[g][r["field"]] = maybe_num(r["value"])
    items = [by_group[g] for g in order]
    if len(items) == 1 and order == [""]:
        return items[0]
    return items


def _date_from_timestamp_ms(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, tz=UTC).strftime("%Y-%m-%d")


def build_btc_spot_daily(nupl_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Extract daily BTC price from NUPL CSV rows (field=price)."""
    ts_to_date: dict[int, str] = {}
    prices_by_ts: dict[int, float] = {}
    for row in nupl_rows:
        if row.get("field") != "price":
            continue
        try:
            ts = int(row["timestamp_ms"])
        except (KeyError, ValueError):
            continue
        val = maybe_num(row["value"])
        if not isinstance(val, (int, float)):
            continue
        prices_by_ts[ts] = float(val)
        raw_date = (row.get("date") or "").strip()
        if raw_date:
            ts_to_date[ts] = raw_date[:10]

    data: list[dict[str, Any]] = []
    for ts in sorted(prices_by_ts.keys()):
        data.append(
            {
                "date": ts_to_date.get(ts) or _date_from_timestamp_ms(ts),
                "timestamp_ms": ts,
                "price": prices_by_ts[ts],
            }
        )
    return data


def write_btc_spot_daily_json(
    nupl_rows: list[dict[str, str]] | None,
    out_path: Path,
) -> None:
    if not nupl_rows:
        print("[skip] btc_spot_daily.json  (no NUPL rows)")
        return
    data = build_btc_spot_daily(nupl_rows)
    if not data:
        print("[skip] btc_spot_daily.json  (no price rows)")
        return
    payload: dict[str, Any] = {
        "code": "0",
        "msg": "success",
        "source": NUPL_ENDPOINT,
        "data": data,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    JM.write_dashboard_json(
        out_path,
        payload,
        merge_data_fn=lambda ex, inc: JM.merge_list_of_dicts(ex, inc, key_field="date"),
    )
    print(f"  wrote btc_spot_daily.json  ({out_path.stat().st_size:,} bytes, {len(data):,} rows)")


def build_vs_m2_growth(rows_by_endpoint: dict[str, list[dict[str, str]]]) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = defaultdict(dict)

    def absorb(rows: list[dict[str, str]], growth_key: str) -> None:
        for r in rows:
            try:
                ts = int(r["timestamp_ms"])
            except (KeyError, ValueError):
                continue
            field = r["field"]
            val = maybe_num(r["value"])
            if val is None:
                continue
            merged[ts]["timestamp"] = ts
            if field == "price":
                merged[ts].setdefault("price", val)
            elif field in ("value", growth_key, "global_m2_yoy_growth", "us_m2_yoy_growth"):
                merged[ts][growth_key] = val

    absorb(rows_by_endpoint.get("bitcoin-vs-global-m2-growth", []), "global_m2_yoy_growth")
    absorb(rows_by_endpoint.get("bitcoin-vs-us-m2-growth", []), "us_m2_yoy_growth")
    return [merged[ts] for ts in sorted(merged.keys())]


def rows_by_endpoint(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        out[r["endpoint"]].append(r)
    return out


def convert_coinglass_csv_to_json(
    csv_path: Path,
    out_root: Path,
    *,
    btc_spot_daily_out: Path | None = None,
) -> None:
    rows = load_csv_dicts(csv_path)
    by_ep = rows_by_endpoint(rows)
    print(f"Loaded {len(rows):,} rows across {len(by_ep)} endpoints from {csv_path}")
    print(f"Writing JSON under {out_root}\n")

    for eid, rel in COINGLASS_ENDPOINT_OUT.items():
        rs = by_ep.get(eid)
        if not rs:
            print(f"[skip] {eid}  (no rows)")
            continue
        print(f"[{eid}] -> {rel}")
        kind = rs[0].get("kind", "series")
        data = build_snapshot_payload(rs) if kind == "snapshot" else build_series_payload(rs)
        merge_fn = JM.merge_snapshot if kind == "snapshot" else JM.merge_pipeline_series
        write_json(out_root, rel, data, merge_fn=merge_fn)

    if any(eid in by_ep for eid in ("bitcoin-vs-global-m2-growth", "bitcoin-vs-us-m2-growth")):
        print("[vs-m2-growth (merged)] -> bitcoin-vs-m2-growth.json")
        write_json(
            out_root,
            "bitcoin-vs-m2-growth.json",
            build_vs_m2_growth(by_ep),
            merge_fn=lambda ex, inc: JM.merge_list_of_dicts(ex, inc, key_field="timestamp"),
        )

    spot_out = btc_spot_daily_out or (DEFAULT_JSON_ROOT / "coinglass" / "btc_spot_daily.json")
    print(f"[{NUPL_ENDPOINT}] -> {spot_out.name} (canonical BTC spot)")
    write_btc_spot_daily_json(by_ep.get(NUPL_ENDPOINT), spot_out)


def metric_name_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def parse_glassnode_endpoint(eid: str) -> tuple[int, str] | None:
    m = GLASSNODE_EP_RE.match(eid.strip())
    if not m:
        return None
    rank = int(m.group(1))
    display = m.group(2).replace("-", " ").strip()
    return rank, display


def build_tv_series_points(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    by_t: dict[int, dict[str, Any]] = defaultdict(dict)
    for r in rows:
        if r.get("kind") != "series":
            continue
        try:
            ts_ms = int(r["timestamp_ms"])
        except (KeyError, ValueError):
            continue
        t = ts_ms // 1000 if ts_ms > 1_000_000_000_000 else ts_ms
        field = (r.get("field") or "value").strip() or "value"
        val = maybe_num(r["value"])
        if val is None:
            continue
        by_t[t][field] = val

    series: list[dict[str, Any]] = []
    for t in sorted(by_t.keys()):
        bag = by_t[t]
        if not bag:
            continue
        if len(bag) == 1:
            v: Any = next(iter(bag.values()))
        else:
            v = json.dumps(bag, separators=(",", ":"))
        series.append({"t": t, "v": v})
    return series


def merge_tv_legacy(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    if not existing:
        return incoming
    by_t: dict[int, dict[str, Any]] = {}
    for p in existing.get("series") or []:
        if isinstance(p, dict) and "t" in p:
            by_t[int(p["t"])] = p
    for p in incoming.get("series") or []:
        if isinstance(p, dict) and "t" in p:
            by_t[int(p["t"])] = p
    merged = dict(incoming)
    merged["rank"] = incoming.get("rank", existing.get("rank"))
    merged["name"] = incoming.get("name", existing.get("name"))
    merged["series"] = [by_t[t] for t in sorted(by_t.keys())]
    return merged


def write_tv_legacy_json(out_root: Path, rel: str, payload: dict[str, Any]) -> None:
    p = out_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = JM.load_json(p) if p.is_file() else None
    merged = merge_tv_legacy(existing if isinstance(existing, dict) else None, payload)
    JM.write_json(p, merged)
    print(f"  wrote {rel}  ({p.stat().st_size:,} bytes)")


def cleanup_json_root(json_root: Path) -> None:
    """Remove stray *.json under output/json/ (belong in coinglass/ or glassnode/)."""
    removed = 0
    for p in sorted(json_root.glob("*.json")):
        p.unlink(missing_ok=True)
        removed += 1
        print(f"  removed stray {p.name}")
    if removed:
        print(f"Cleaned {removed} file(s) from {json_root}\n")


def migrate_legacy_root_json(json_root: Path) -> None:
    """One-time layout fix: move root JSON into coinglass/ or glassnode/."""
    cg_dir = json_root / "coinglass"
    gn_dir = json_root / "glassnode"
    cg_dir.mkdir(parents=True, exist_ok=True)
    gn_dir.mkdir(parents=True, exist_ok=True)

    for p in sorted(json_root.glob("*.json")):
        name = p.name
        if name == "btc_spot_daily.json":
            dest = cg_dir / name
        elif re.match(r"^\d{3}_", name):
            dest = gn_dir / name
        else:
            dest = cg_dir / name
        if dest.exists() and dest.resolve() != p.resolve():
            p.unlink()
            print(f"  dropped duplicate root {name}")
            continue
        p.rename(dest)
        print(f"  moved {name} -> {dest.relative_to(json_root)}")


def prune_glassnode_pipeline_json(out_root: Path) -> None:
    for p in sorted(out_root.glob("glassnode-*.json")):
        p.unlink(missing_ok=True)
        print(f"  removed legacy pipeline {p.name}")


def glassnode_allowed_ranks() -> set[int]:
    return {spec.rank for spec in METRIC_SPECS}


def prune_glassnode_tv_json(out_root: Path, allowed_ranks: set[int]) -> None:
    """Remove TV JSON files whose rank is not in the curated catalog."""
    removed = 0
    for p in sorted(out_root.glob("*.json")):
        m = re.match(r"^(\d{3})_", p.name)
        if not m:
            continue
        rank = int(m.group(1))
        if rank in allowed_ranks:
            continue
        p.unlink(missing_ok=True)
        removed += 1
        print(f"  removed {p.name}")
    if removed:
        print(f"Pruned {removed} glassnode JSON file(s) not in catalog\n")


def convert_glassnode_csv_to_json(csv_path: Path, out_root: Path) -> None:
    allowed_ranks = glassnode_allowed_ranks()
    rows = load_csv_dicts(csv_path)
    by_ep = rows_by_endpoint(rows)
    print(f"Loaded {len(rows):,} rows across {len(by_ep)} endpoints from {csv_path}")
    print(f"Writing TV JSON under {out_root} (catalog: {len(allowed_ranks)} metrics)\n")

    for eid, rs in sorted(by_ep.items()):
        parsed = parse_glassnode_endpoint(eid)
        if not parsed:
            print(f"[skip] {eid}  (unrecognized endpoint id)")
            continue
        rank, display_name = parsed
        if rank not in allowed_ranks:
            print(f"[skip] {eid}  (rank {rank:03d} not in catalog)")
            continue
        kind = rs[0].get("kind", "series")
        if kind != "series":
            print(f"[skip] {eid}  (kind={kind})")
            continue
        series = build_tv_series_points(rs)
        if not series:
            print(f"[skip] {eid}  (empty series)")
            continue
        slug = metric_name_slug(display_name)
        rel = f"{rank:03d}_{slug}.json"
        print(f"[{eid}] -> {rel}")
        write_tv_legacy_json(
            out_root,
            rel,
            {"rank": rank, "name": display_name, "series": series},
        )

    prune_glassnode_pipeline_json(out_root)
    prune_glassnode_tv_json(out_root, allowed_ranks)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--coinglass-in", default=str(DEFAULT_COINGLASS_IN))
    parser.add_argument("--coinglass-out", default=str(DEFAULT_COINGLASS_OUT))
    parser.add_argument("--btc-spot-daily-out", default=str(DEFAULT_BTC_SPOT_DAILY_OUT))
    parser.add_argument("--glassnode-in", default=str(DEFAULT_GLASSNODE_IN))
    parser.add_argument("--glassnode-out", default=str(DEFAULT_GLASSNODE_OUT))
    parser.add_argument("--skip-coinglass", action="store_true")
    parser.add_argument("--skip-glassnode", action="store_true")
    args = parser.parse_args()

    if not args.skip_coinglass:
        cg_csv = Path(args.coinglass_in).resolve()
        if not cg_csv.exists():
            raise SystemExit(f"Coinglass CSV not found: {cg_csv}")
        cg_out = Path(args.coinglass_out).resolve()
        cg_out.mkdir(parents=True, exist_ok=True)
        print("=== Coinglass CSV -> JSON ===\n")
        convert_coinglass_csv_to_json(
            cg_csv,
            cg_out,
            btc_spot_daily_out=Path(args.btc_spot_daily_out).resolve(),
        )

    if not args.skip_glassnode:
        gn_csv = Path(args.glassnode_in).resolve()
        if not gn_csv.exists():
            print(f"\n[skip] Glassnode CSV not found: {gn_csv}")
        else:
            gn_out = Path(args.glassnode_out).resolve()
            gn_out.mkdir(parents=True, exist_ok=True)
            print("\n=== Glassnode CSV -> JSON ===\n")
            convert_glassnode_csv_to_json(gn_csv, gn_out)

    json_root = DEFAULT_JSON_ROOT
    migrate_legacy_root_json(json_root)
    cleanup_json_root(json_root)

    print("\nDONE.")


if __name__ == "__main__":
    main()
