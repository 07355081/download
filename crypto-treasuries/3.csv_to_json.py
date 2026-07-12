"""Step 3/3 — Convert crypto-treasuries cache payloads into dashboard-ready JSON files.

Requires: 1.download.py and 2.mark_holdings.py (or `python run_all.py`).

Reads:
  output/holdings.csv (skip_json=1 的行不转化)
  cache/overview_<coin_id>.json
  cache/entity_<entity_id>.json
  cache/entity_chart_<entity_id>_<coin_id>_<days>.json
  cache/entity_transactions_<entity_id>_<coin_id>.json

Writes (flat files):
  output/json/overview_<coin_id>.json
  output/json/entity_<entity_id>_<coin_id>_<days>.json
  output/json/index.json

Also removes output/json/entity_<id>_*.json when entity_id is skip_json=1 in holdings.csv.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
DATA_DOWNLOAD_ROOT = HERE.parent
if str(DATA_DOWNLOAD_ROOT) not in sys.path:
    sys.path.insert(0, str(DATA_DOWNLOAD_ROOT))
import _json_merge as JM  # noqa: E402
CACHE_DIR = HERE / "cache"
JSON_OUT_DIR = HERE / "output" / "json"
from holdings_csv import HOLDINGS_CSV  # noqa: E402

OVERVIEW_RE = re.compile(r"^overview_(?P<coin>[a-z0-9_-]+)\.json$")
DETAIL_RE = re.compile(r"^entity_(?!chart_|transactions_)(?P<entity>[a-z0-9_-]+)\.json$")
CHART_RE = re.compile(r"^entity_chart_(?P<entity>[a-z0-9_-]+)_(?P<coin>[a-z0-9_-]+)_(?P<days>[a-z0-9_-]+)\.json$")
TX_RE = re.compile(r"^entity_transactions_(?P<entity>[a-z0-9_-]+)_(?P<coin>[a-z0-9_-]+)\.json$")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any, *, merge_data: bool = True) -> None:
    if merge_data and isinstance(payload, dict) and "data" in payload:
        JM.write_dashboard_json(path, payload, merge_data_fn=JM.merge_nested_dict)
    else:
        JM.write_json(path, payload)


def load_skip_entity_ids() -> Set[str]:
    """读取 holdings.csv 中 skip_json=1（标红）的 entity_id。"""
    skip: Set[str] = set()
    if not HOLDINGS_CSV.exists():
        return skip
    with HOLDINGS_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if str(row.get("skip_json") or "").strip() not in ("1", "true", "yes", "Y"):
                continue
            eid = str(row.get("entity_id") or "").strip()
            if eid:
                skip.add(eid)
    return skip


def purge_skipped_entity_jsons(skip: Set[str]) -> int:
    """删除已标红实体此前生成的 entity_<id>_*.json。"""
    if not skip:
        return 0
    removed = 0
    for entity_id in skip:
        for path in JSON_OUT_DIR.glob(f"entity_{entity_id}_*.json"):
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def strip_skipped_from_overview(overview: Dict[str, Any], skip: Set[str]) -> Dict[str, Any]:
    if not skip:
        return overview
    companies = [r for r in overview.get("companies", []) if r.get("entity_id") not in skip]
    governments = [r for r in overview.get("governments", []) if r.get("entity_id") not in skip]
    out = dict(overview)
    out["companies"] = companies
    out["governments"] = governments
    summary = dict(out.get("summary") or {})
    summary["company_count"] = len(companies)
    summary["government_count"] = len(governments)
    out["summary"] = summary
    return out


def normalize_entity_payload(
    detail: Dict[str, Any], coin_id: str, chart: Dict[str, Any], tx_payload: Dict[str, Any]
) -> Dict[str, Any]:
    holdings = detail.get("holdings", []) if isinstance(detail.get("holdings"), list) else []
    selected_holding = next((h for h in holdings if h.get("coin_id") == coin_id), None)
    chart_norm = {
        "holdings": chart.get("holdings", []) if isinstance(chart.get("holdings"), list) else [],
        "holding_value_in_usd": chart.get("holding_value_in_usd", [])
        if isinstance(chart.get("holding_value_in_usd"), list)
        else [],
    }
    transactions = tx_payload.get("transactions", []) if isinstance(tx_payload.get("transactions"), list) else []
    return {
        "entity": detail,
        "selected_holding": selected_holding,
        "chart": chart_norm,
        "transactions": transactions,
        "fetched_at": detail.get("fetched_at") or tx_payload.get("fetched_at") or chart.get("fetched_at"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coin", default=None, help="Subset coin ids, comma-separated")
    parser.add_argument("--days", default=None, help="Subset days, comma-separated")
    args = parser.parse_args()

    if not CACHE_DIR.exists():
        raise SystemExit(f"cache dir not found: {CACHE_DIR}")
    JSON_OUT_DIR.mkdir(parents=True, exist_ok=True)

    coin_filter = set(c.strip() for c in args.coin.split(",")) if args.coin else None
    day_filter = set(d.strip() for d in args.days.split(",")) if args.days else None

    overviews: Dict[str, Dict[str, Any]] = {}
    details: Dict[str, Dict[str, Any]] = {}
    charts: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    txs: Dict[tuple[str, str], Dict[str, Any]] = {}

    for cf in sorted(CACHE_DIR.glob("*.json")):
        name = cf.name
        if name == "entity_catalog.json":
            continue

        m = OVERVIEW_RE.match(name)
        if m:
            coin = m.group("coin")
            if coin_filter and coin not in coin_filter:
                continue
            overviews[coin] = read_json(cf)
            continue

        m = DETAIL_RE.match(name)
        if m:
            entity = m.group("entity")
            details[entity] = read_json(cf)
            continue

        m = CHART_RE.match(name)
        if m:
            entity, coin, days = m.group("entity"), m.group("coin"), m.group("days")
            if coin_filter and coin not in coin_filter:
                continue
            if day_filter and days not in day_filter:
                continue
            charts[(entity, coin, days)] = read_json(cf)
            continue

        m = TX_RE.match(name)
        if m:
            entity, coin = m.group("entity"), m.group("coin")
            if coin_filter and coin not in coin_filter:
                continue
            txs[(entity, coin)] = read_json(cf)
            continue

    if not HOLDINGS_CSV.exists():
        raise SystemExit(f"缺少 {HOLDINGS_CSV}，请先运行 1.download.py 与 2.mark_holdings.py")

    skip_entity_ids = load_skip_entity_ids()
    print(f"holdings.csv: skip_json=1 -> {len(skip_entity_ids)} entities excluded")
    purged = purge_skipped_entity_jsons(skip_entity_ids)
    if purged:
        print(f"removed {purged} entity JSON file(s) for skipped entities")

    written = 0
    index_payload: Dict[str, Any] = {"coins": []}

    for coin, overview in sorted(overviews.items()):
        overview = strip_skipped_from_overview(overview, skip_entity_ids)
        payload = {"code": 0, "msg": "success", "data": overview}
        out = JSON_OUT_DIR / f"overview_{coin}.json"
        write_json(out, payload, merge_data=False)
        written += 1

        entity_ids = sorted(
            {
                r.get("entity_id")
                for r in (overview.get("companies", []) + overview.get("governments", []))
                if isinstance(r, dict) and r.get("entity_id")
            }
        )
        day_values = sorted({days for (entity, c, days) in charts.keys() if c == coin and entity in entity_ids})
        index_payload["coins"].append(
            {
                "coin_id": coin,
                "overview_file": out.name,
                "entity_count": len(entity_ids),
                "days": day_values,
            }
        )

        for entity_id in entity_ids:
            if entity_id in skip_entity_ids:
                continue
            detail = details.get(entity_id)
            tx = txs.get((entity_id, coin), {})
            if not detail:
                continue
            available_days = sorted({days for (e, c, days) in charts.keys() if e == entity_id and c == coin})
            for days in available_days:
                if day_filter and days not in day_filter:
                    continue
                chart = charts.get((entity_id, coin, days))
                if not chart:
                    continue
                entity_payload = normalize_entity_payload(detail, coin, chart, tx)
                wrapped = {"code": 0, "msg": "success", "data": entity_payload}
                out = JSON_OUT_DIR / f"entity_{entity_id}_{coin}_{days}.json"
                write_json(out, wrapped)
                written += 1

    write_json(JSON_OUT_DIR / "index.json", index_payload, merge_data=False)
    written += 1

    print(f"DONE. wrote={written} files -> {JSON_OUT_DIR}")


if __name__ == "__main__":
    main()

