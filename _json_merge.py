"""Shared JSON merge helpers: incoming non-empty wins, else keep existing JSON."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None  # type: ignore[assignment]


def is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return True
    if pd is not None:
        try:
            if pd.isna(value):
                return True
        except (TypeError, ValueError):
            pass
    return False


def load_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def merge_value(incoming: Any, existing: Any) -> Any:
    return incoming if not is_empty(incoming) else existing


def merge_dict_fields(
    incoming: dict[str, Any],
    existing: dict[str, Any],
    *,
    keys: list[str] | None = None,
) -> dict[str, Any]:
    fields = keys if keys is not None else sorted(set(existing) | set(incoming))
    out: dict[str, Any] = {}
    for key in fields:
        out[key] = merge_value(incoming.get(key), existing.get(key))
    return out


def merge_candles(
    existing: list[dict[str, Any]] | None,
    incoming: list[dict[str, Any]],
    *,
    time_key: str = "time",
) -> list[dict[str, Any]]:
    if not incoming:
        return list(existing or [])
    if not existing:
        return [dict(c) for c in incoming]

    by_time: dict[Any, dict[str, Any]] = {c[time_key]: dict(c) for c in existing if time_key in c}
    for candle in incoming:
        ts = candle.get(time_key)
        if ts is None:
            continue
        if ts not in by_time:
            by_time[ts] = dict(candle)
            continue
        merged = dict(by_time[ts])
        for key, value in candle.items():
            if key == time_key:
                continue
            if not is_empty(value):
                merged[key] = value
        by_time[ts] = merged
    return [by_time[ts] for ts in sorted(by_time.keys())]


def merge_wrapped_data(
    existing_payload: dict[str, Any] | None,
    incoming_payload: dict[str, Any],
    merge_data_fn,
    *,
    data_key: str = "data",
) -> dict[str, Any]:
    incoming_data = incoming_payload.get(data_key)
    if existing_payload is None or not isinstance(existing_payload, dict):
        return incoming_payload
    existing_data = existing_payload.get(data_key)
    merged_data = merge_data_fn(existing_data, incoming_data)
    out = dict(existing_payload)
    out.update(incoming_payload)
    out[data_key] = merged_data
    return out


def write_coinglass_candles_json(path: Path, payload: dict[str, Any]) -> None:
    existing = load_json(path)
    merged = merge_wrapped_data(existing, payload, merge_candles)
    write_json(path, merged)


def _absorb_pipeline_series(
    target: dict[int, dict[str, Any]],
    src: dict[str, Any] | None,
    *,
    prefer_incoming: bool,
) -> None:
    if not isinstance(src, dict):
        return
    time_list = src.get("time_list") or []
    data_list = src.get("data_list") or []
    price_list = src.get("price_list")
    for i, ts in enumerate(time_list):
        slot = target.setdefault(ts, {"data": None, "price": None})
        if i < len(data_list):
            value = data_list[i]
            if prefer_incoming:
                if not is_empty(value):
                    slot["data"] = value
            elif is_empty(slot["data"]) and not is_empty(value):
                slot["data"] = value
        if isinstance(price_list, list) and i < len(price_list):
            value = price_list[i]
            if prefer_incoming:
                if not is_empty(value):
                    slot["price"] = value
            elif is_empty(slot["price"]) and not is_empty(value):
                slot["price"] = value


def merge_pipeline_series(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    if not incoming and not existing:
        return {"time_list": [], "data_list": []}
    if not incoming:
        return dict(existing or {"time_list": [], "data_list": []})
    if not existing:
        return dict(incoming)

    merged: dict[int, dict[str, Any]] = {}
    _absorb_pipeline_series(merged, existing, prefer_incoming=False)
    _absorb_pipeline_series(merged, incoming, prefer_incoming=True)
    if not merged:
        return {"time_list": [], "data_list": []}

    sorted_ts = sorted(merged.keys())
    data_list = [merged[ts]["data"] for ts in sorted_ts]
    has_price = any(not is_empty(merged[ts]["price"]) for ts in sorted_ts)
    out: dict[str, Any] = {"time_list": sorted_ts, "data_list": data_list}
    if has_price:
        out["price_list"] = [merged[ts]["price"] for ts in sorted_ts]
    return out


def merge_snapshot(existing: Any, incoming: Any) -> Any:
    if incoming is None or incoming == {} or incoming == []:
        return existing if existing is not None else incoming
    if existing is None:
        return incoming
    if isinstance(incoming, dict) and isinstance(existing, dict):
        return merge_dict_fields(incoming, existing)
    if isinstance(incoming, list) and isinstance(existing, list):
        if not incoming:
            return existing
        if not existing:
            return incoming
        if all(isinstance(x, dict) for x in incoming + existing):
            keyed_in = {str(i.get("group", i)): i for i in incoming if isinstance(i, dict)}
            keyed_ex = {str(i.get("group", i)): i for i in existing if isinstance(i, dict)}
            if keyed_in and keyed_ex and (len(keyed_in) == len(incoming)):
                all_keys = sorted(set(keyed_ex) | set(keyed_in))
                return [merge_dict_fields(keyed_in.get(k, {}), keyed_ex.get(k, {})) for k in all_keys]
        out = list(existing)
        for i, item in enumerate(incoming):
            if i < len(out) and isinstance(item, dict) and isinstance(out[i], dict):
                out[i] = merge_dict_fields(item, out[i])
            elif not is_empty(item):
                if i >= len(out):
                    out.append(item)
                else:
                    out[i] = item
        return out
    return incoming if not is_empty(incoming) else existing


def merge_list_of_dicts(
    existing: list[dict[str, Any]] | None,
    incoming: list[dict[str, Any]],
    *,
    key_field: str | None = None,
    key_fields: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    if not incoming:
        return list(existing or [])
    if not existing:
        return [dict(row) for row in incoming]

    if key_fields:
        def row_key(row: dict[str, Any]) -> tuple[Any, ...]:
            return tuple(row.get(k) for k in key_fields)
    elif key_field:
        def row_key(row: dict[str, Any]) -> Any:
            return row.get(key_field)
    else:
        raise ValueError("key_field or key_fields is required")

    by_key: dict[Any, dict[str, Any]] = {
        row_key(row): dict(row) for row in existing if all(not is_empty(row.get(k)) for k in (key_fields or (key_field,)))
    }
    for row in incoming:
        key = row_key(row)
        if any(is_empty(row.get(k)) for k in (key_fields or (key_field,))):
            continue
        if key not in by_key:
            by_key[key] = dict(row)
            continue
        by_key[key] = merge_dict_fields(row, by_key[key])
    return [by_key[k] for k in sorted(by_key.keys(), key=lambda x: str(x))]


def merge_wide_chart_data(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    if not incoming and not existing:
        return {"time_list": [], "price_list": [], "data_map": {}}
    if not incoming:
        return dict(existing or {"time_list": [], "price_list": [], "data_map": {}})
    if not existing:
        return dict(incoming)

    def index_series(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        time_list = payload.get("time_list") or []
        price_list = payload.get("price_list") or []
        data_map = payload.get("data_map") or {}
        for i, ts in enumerate(time_list):
            slot = out.setdefault(int(ts), {"price": None, "series": {}})
            if isinstance(price_list, list) and i < len(price_list):
                slot["price"] = price_list[i]
            if isinstance(data_map, dict):
                for ex, series in data_map.items():
                    if isinstance(series, list) and i < len(series):
                        slot["series"][str(ex)] = series[i]
        return out

    merged: dict[int, dict[str, Any]] = {}
    for ts, slot in index_series(existing).items():
        merged[ts] = {"price": slot.get("price"), "series": dict(slot.get("series") or {})}
    for ts, slot in index_series(incoming).items():
        target = merged.setdefault(ts, {"price": None, "series": {}})
        if not is_empty(slot.get("price")):
            target["price"] = slot["price"]
        for ex, value in (slot.get("series") or {}).items():
            if not is_empty(value):
                target["series"][ex] = value

    times = sorted(merged.keys())
    keys = sorted({ex for slot in merged.values() for ex in slot["series"].keys()})
    data_map = {ex: [] for ex in keys}
    price_list: list[Any] = []
    for ts in times:
        slot = merged[ts]
        price_list.append(slot.get("price"))
        for ex in keys:
            data_map[ex].append(slot["series"].get(ex))
    return {"time_list": times, "price_list": price_list, "data_map": data_map}


def merge_dvol_rows(existing: list[list[Any]] | None, incoming: list[list[Any]]) -> list[list[Any]]:
    if not incoming:
        return list(existing or [])
    if not existing:
        return [list(row) for row in incoming]

    by_date: dict[str, list[Any]] = {}
    for row in existing:
        if row:
            by_date[str(row[0])] = list(row)
    for row in incoming:
        if not row:
            continue
        date_key = str(row[0])
        if date_key not in by_date:
            by_date[date_key] = list(row)
            continue
        merged = list(by_date[date_key])
        for i in range(1, max(len(row), len(merged))):
            new_v = row[i] if i < len(row) else None
            old_v = merged[i] if i < len(merged) else None
            while len(merged) <= i:
                merged.append(None)
            merged[i] = new_v if not is_empty(new_v) else old_v
        by_date[date_key] = merged
    return [by_date[k] for k in sorted(by_date.keys())]


def merge_etf_premium_data(
    existing: list[dict[str, Any]] | None,
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not incoming:
        return list(existing or [])
    if not existing:
        return [dict(bucket) for bucket in incoming]

    def index_buckets(buckets: list[dict[str, Any]]) -> dict[int, dict[str, dict[str, Any]]]:
        out: dict[int, dict[str, dict[str, Any]]] = {}
        for bucket in buckets:
            ts = bucket.get("timestamp")
            if ts is None:
                continue
            tickers: dict[str, dict[str, Any]] = {}
            for item in bucket.get("list") or []:
                if isinstance(item, dict) and item.get("ticker"):
                    tickers[str(item["ticker"])] = dict(item)
            out[int(ts)] = tickers
        return out

    merged = index_buckets(existing)
    incoming_idx = index_buckets(incoming)
    for ts, tickers in incoming_idx.items():
        slot = merged.setdefault(ts, {})
        for ticker, fields in tickers.items():
            if ticker not in slot:
                slot[ticker] = dict(fields)
            else:
                slot[ticker] = merge_dict_fields(fields, slot[ticker])
    data: list[dict[str, Any]] = []
    for ts in sorted(merged.keys()):
        sub = [{"ticker": tk, **fields} for tk, fields in sorted(merged[ts].items())]
        data.append({"timestamp": ts, "list": sub})
    return data


def merge_nested_dict(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    if not incoming:
        return dict(existing or {})
    if not existing:
        return dict(incoming)
    out = dict(existing)
    for key, value in incoming.items():
        old = existing.get(key)
        if isinstance(value, dict) and isinstance(old, dict):
            out[key] = merge_nested_dict(old, value)
        elif isinstance(value, list) and isinstance(old, list):
            if value and isinstance(value[0], dict):
                key_field = None
                for candidate in ("entity_id", "coin_id", "symbol", "ticker", "id", "date"):
                    if candidate in value[0]:
                        key_field = candidate
                        break
                if key_field:
                    out[key] = merge_list_of_dicts(old, value, key_field=key_field)
                else:
                    out[key] = value if value else old
            else:
                out[key] = value if value else old
        elif not is_empty(value):
            out[key] = value
    return out


def write_dashboard_json(
    path: Path,
    incoming_payload: dict[str, Any],
    *,
    merge_data_fn,
    data_key: str = "data",
) -> None:
    existing = load_json(path)
    merged = merge_wrapped_data(existing, incoming_payload, merge_data_fn, data_key=data_key)
    write_json(path, merged)


# ── pandas wide-table merge (cex-asset&vol) ───────────────────────────

def normalize_wide_df(df: "pd.DataFrame") -> "pd.DataFrame":
    if df.empty:
        return pd.DataFrame(columns=["date"])

    date_col = "date" if "date" in df.columns else df.columns[0]
    out = df.copy()
    out = out.rename(columns={date_col: "date"})
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    out = out.dropna(subset=["date"]).copy()

    value_cols = [c for c in out.columns if c != "date"]
    for col in value_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.sort_values("date")


def normalize_monthly_wide_df(df: "pd.DataFrame") -> "pd.DataFrame":
    if df.empty:
        return pd.DataFrame(columns=["month"])

    month_col = "month" if "month" in df.columns else df.columns[0]
    out = df.copy()
    out = out.rename(columns={month_col: "month"})
    out["month"] = out["month"].astype(str).str.strip()
    out = out.dropna(subset=["month"]).copy()

    value_cols = [c for c in out.columns if c != "month"]
    for col in value_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.sort_values("month")


def load_existing_json_df(json_path: Path, *, period_col: str = "date") -> "pd.DataFrame | None":
    if pd is None or not json_path.is_file():
        return None
    payload = load_json(json_path)
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        return None
    frame = pd.DataFrame(rows)
    if period_col == "month":
        return normalize_monthly_wide_df(frame)
    return normalize_wide_df(frame)


def merge_wide_prefer_csv(
    existing: "pd.DataFrame | None",
    incoming: "pd.DataFrame",
    *,
    period_col: str = "date",
) -> tuple["pd.DataFrame", dict[str, int]]:
    if pd is None:
        raise RuntimeError("pandas is required for merge_wide_prefer_csv")
    normalize = normalize_monthly_wide_df if period_col == "month" else normalize_wide_df
    incoming = normalize(incoming)
    if existing is None or existing.empty:
        return incoming, {"from_csv": 0, "from_json": 0, "empty": 0}

    existing = normalize(existing)
    all_periods = sorted(
        set(existing[period_col].astype(str)) | set(incoming[period_col].astype(str))
    )
    all_cols = sorted(
        {c for c in existing.columns if c != period_col}
        | {c for c in incoming.columns if c != period_col}
    )

    ex = existing.set_index(period_col)
    inc = incoming.set_index(period_col)
    out_rows: list[dict[str, Any]] = []
    stats = {"from_csv": 0, "from_json": 0, "empty": 0}

    for period in all_periods:
        row: dict[str, Any] = {period_col: period}
        for col in all_cols:
            new_v = pd.NA
            old_v = pd.NA
            if period in inc.index and col in inc.columns:
                new_v = inc.at[period, col]
            if period in ex.index and col in ex.columns:
                old_v = ex.at[period, col]
            if pd.notna(new_v):
                row[col] = float(new_v)
                stats["from_csv"] += 1
            elif pd.notna(old_v):
                row[col] = float(old_v)
                stats["from_json"] += 1
            else:
                row[col] = pd.NA
                stats["empty"] += 1
        out_rows.append(row)

    return pd.DataFrame(out_rows)[[period_col] + all_cols], stats
