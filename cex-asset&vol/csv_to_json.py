# -*- coding: utf-8 -*-
"""Export dashboard JSON with per-cell merge: CSV/XLSX overrides, else prior JSON."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from _paths import (
    ASSET_WIDE_CSV,
    BTC_BALANCE_CSV,
    ETH_BALANCE_CSV,
    OUTPUT_JSON_DIR,
    PIPELINE_XLSX,
    STABLECOIN_BALANCE_CSV,
    ensure_cache_layout,
)
from _closed_months import list_closed_months, period_is_closed

DEFAULT_PIPELINE_XLSX = PIPELINE_XLSX
DEFAULT_ASSET_CSV = ASSET_WIDE_CSV
DEFAULT_BTC_BALANCE_CSV = BTC_BALANCE_CSV
DEFAULT_ETH_BALANCE_CSV = ETH_BALANCE_CSV
DEFAULT_STABLECOIN_BALANCE_CSV = STABLECOIN_BALANCE_CSV

# metric → default CSV path (coin-balance series from DefiLlama tokens)
BALANCE_METRICS: list[tuple[str, Path]] = [
    ("cex_exchange_daily_balance_btc", DEFAULT_BTC_BALANCE_CSV),
    ("cex_exchange_daily_balance_eth", DEFAULT_ETH_BALANCE_CSV),
    ("cex_exchange_daily_balance_stablecoins", DEFAULT_STABLECOIN_BALANCE_CSV),
]
SPOT_MONTHLY_SHEET = "现货月度"
FUTURES_MONTHLY_SHEET = "合约月度"
TOTAL_COL = "ALL"
EXCHANGE_COL_NAMES = ("交易所", "exchange", "Exchange")
EXCHANGE_RENAMES = {
    "Binance CEX": "Binance",
    "Crypto-com": "Crypto.com",
}
MIN_VALID_DAYS_PER_MONTH = 28


def read_csv_with_fallback(path: Path) -> pd.DataFrame:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "cp936"):
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path)


def _apply_exchange_renames(out: pd.DataFrame, period_col: str) -> pd.DataFrame:
    for old_name, new_name in EXCHANGE_RENAMES.items():
        if old_name not in out.columns:
            continue
        if new_name in out.columns:
            out[new_name] = pd.to_numeric(out[old_name], errors="coerce").combine_first(
                pd.to_numeric(out[new_name], errors="coerce")
            )
            out = out.drop(columns=[old_name], errors="ignore")
        else:
            out = out.rename(columns={old_name: new_name})
    value_cols = [c for c in out.columns if c != period_col]
    return out[[period_col] + value_cols]


def normalize_wide_df(df: pd.DataFrame) -> pd.DataFrame:
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
    out = _apply_exchange_renames(out, "date")
    return out.sort_values("date")


def normalize_monthly_wide_df(df: pd.DataFrame) -> pd.DataFrame:
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
    out = _apply_exchange_renames(out, "month")
    return out.sort_values("month")


def load_existing_json_df(json_path: Path, *, period_col: str = "date") -> pd.DataFrame | None:
    """Load prior dashboard JSON wide table, if present."""
    if not json_path.is_file():
        return None
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
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
    existing: pd.DataFrame | None,
    incoming: pd.DataFrame,
    *,
    period_col: str = "date",
    freeze_closed: bool = False,
    closed_months: set[str] | None = None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Per-cell merge: incoming wins when non-null; else keep existing JSON.

    When freeze_closed=True, periods in closed_months keep prior JSON values
    (prior wins even if incoming is non-null).
    """
    normalize = normalize_monthly_wide_df if period_col == "month" else normalize_wide_df
    incoming = normalize(incoming)
    closed = closed_months or set()
    if existing is None or existing.empty:
        return incoming, {"from_csv": 0, "from_json": 0, "empty": 0, "frozen": 0}

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
    stats = {"from_csv": 0, "from_json": 0, "empty": 0, "frozen": 0}

    for period in all_periods:
        row: dict[str, Any] = {period_col: period}
        is_closed = bool(freeze_closed and period_is_closed(period, closed))
        for col in all_cols:
            new_v = pd.NA
            old_v = pd.NA
            if period in inc.index and col in inc.columns:
                new_v = inc.at[period, col]
            if period in ex.index and col in ex.columns:
                old_v = ex.at[period, col]
            if is_closed and pd.notna(old_v):
                row[col] = float(old_v)
                stats["frozen"] += 1
            elif pd.notna(new_v):
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


def build_daily_volume_usd_from_xlsx(path: Path, sheet_name: str) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(f"pipeline workbook not found: {path}")
    raw = pd.read_excel(path, sheet_name=sheet_name)
    return normalize_wide_df(raw)


def append_total_column(
    df: pd.DataFrame,
    total_col: str = TOTAL_COL,
    *,
    period_col: str = "date",
) -> pd.DataFrame:
    """在宽表末尾追加总量列（period 外所有数值列求和）。"""
    normalize = normalize_monthly_wide_df if period_col == "month" else normalize_wide_df
    out = normalize(df)
    value_cols = [c for c in out.columns if c not in (period_col, total_col)]
    value_df = out[value_cols].apply(pd.to_numeric, errors="coerce")
    out[total_col] = value_df.sum(axis=1, min_count=1)
    return out[[period_col] + value_cols + [total_col]]


def count_valid_days_per_month(df: pd.DataFrame, date_column: str = "date") -> pd.Series:
    """每月至少一个交易所成交量 > 0 的日历日数量（0 占位不计入）。"""
    tmp = normalize_wide_df(df) if date_column == "date" else df.copy()
    tmp[date_column] = pd.to_datetime(tmp[date_column], errors="coerce")
    value_cols = [c for c in tmp.columns if c != date_column]
    numeric = tmp[value_cols].apply(pd.to_numeric, errors="coerce")
    valid_row = (numeric > 0).any(axis=1) & tmp[date_column].notna()
    tmp = tmp.loc[valid_row]
    if tmp.empty:
        return pd.Series(dtype=int)
    return tmp.groupby(tmp[date_column].dt.to_period("M")).size()


def filter_monthly_by_daily_valid_days(
    monthly_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    *,
    period_col: str = "month",
    date_col: str = "date",
    min_valid_days: int = MIN_VALID_DAYS_PER_MONTH,
) -> pd.DataFrame:
    """保留完整月；流水线日度覆盖但不足 min_valid_days 的月份剔除；无日度覆盖的保留旧 JSON。"""
    monthly = normalize_monthly_wide_df(monthly_df)
    if monthly.empty:
        return monthly

    day_counts = count_valid_days_per_month(daily_df, date_col)

    def keep_month(month_str: str) -> bool:
        try:
            period = pd.Period(month_str, freq="M")
        except ValueError:
            return True
        if period not in day_counts.index:
            return True
        return int(day_counts[period]) >= min_valid_days

    months = monthly[period_col].astype(str)
    keep_mask = months.map(keep_month)
    dropped = sorted(set(months[~keep_mask]))
    if dropped:
        print(
            f"  [monthly filter] drop (<{min_valid_days} positive-volume days in pipeline daily): "
            + ", ".join(dropped)
        )
    return monthly.loc[keep_mask].copy()


def build_monthly_volume_from_xlsx(path: Path, sheet_name: str) -> pd.DataFrame:
    """Read step-4 workbook sheet (rows=exchange, cols=month) -> rows=month wide table."""
    if not path.exists():
        raise SystemExit(f"pipeline workbook not found: {path}")
    raw = pd.read_excel(path, sheet_name=sheet_name)
    exchange_col = next((c for c in raw.columns if str(c) in EXCHANGE_COL_NAMES), raw.columns[0])
    wide = raw.set_index(exchange_col).T.reset_index()
    wide = wide.rename(columns={"index": "month"})
    return normalize_monthly_wide_df(wide)


def build_futures_spot_ratio_df(spot_df: pd.DataFrame, futures_df: pd.DataFrame) -> pd.DataFrame:
    """按交易所生成日度合约/现货比值，并附加全市场总比值。"""
    spot = normalize_wide_df(spot_df)
    futures = normalize_wide_df(futures_df)
    common_exchanges = [c for c in spot.columns if c != "date" and c in futures.columns]
    merged = spot[["date"] + common_exchanges].merge(
        futures[["date"] + common_exchanges],
        on="date",
        how="inner",
        suffixes=("_spot", "_futures"),
    )
    out = pd.DataFrame({"date": merged["date"]})

    for ex in common_exchanges:
        spot_col = pd.to_numeric(merged[f"{ex}_spot"], errors="coerce")
        futures_col = pd.to_numeric(merged[f"{ex}_futures"], errors="coerce")
        ratio = futures_col / spot_col.replace(0, pd.NA)
        out[ex] = ratio.replace([float("inf"), float("-inf")], pd.NA)

    if common_exchanges:
        spot_total = merged[[f"{ex}_spot" for ex in common_exchanges]].apply(pd.to_numeric, errors="coerce").sum(
            axis=1, min_count=1
        )
        futures_total = merged[[f"{ex}_futures" for ex in common_exchanges]].apply(
            pd.to_numeric, errors="coerce"
        ).sum(axis=1, min_count=1)
        total_ratio = futures_total / spot_total.replace(0, pd.NA)
        out["Total"] = total_ratio.replace([float("inf"), float("-inf")], pd.NA)
    return out


def build_payload(
    metric: str,
    source_csv: str,
    df: pd.DataFrame,
    *,
    period_col: str = "date",
) -> dict[str, Any]:
    exchanges = [c for c in df.columns if c != period_col]
    rows: list[dict[str, Any]] = []

    for _, row in df.iterrows():
        item: dict[str, Any] = {period_col: row[period_col]}
        for ex in exchanges:
            value = row[ex]
            item[ex] = None if pd.isna(value) else float(value)
        rows.append(item)

    return {
        "metric": metric,
        "source_csv": source_csv,
        "exchanges": exchanges,
        "row_count": len(rows),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rows": rows,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert cex-asset&vol core artifacts to dashboard-ready JSON")
    parser.add_argument(
        "--pipeline-xlsx",
        default=str(DEFAULT_PIPELINE_XLSX),
        help="Workbook from step 4 (cex_volume_pipeline_unified.xlsx)",
    )
    parser.add_argument("--asset-csv", default=str(DEFAULT_ASSET_CSV), help="Daily exchange assets CSV path")
    parser.add_argument(
        "--skip-volume",
        action="store_true",
        help="Only convert USD assets + coin-balance CSVs (no CoinGecko volume workbook needed)",
    )
    return parser.parse_args()


def merged_source_label(file_name: str) -> str:
    return f"{file_name} (merge: csv/xlsx overrides, else prior json)"


def load_index_datasets(index_json: Path) -> list[dict[str, Any]]:
    if not index_json.is_file():
        return []
    try:
        payload = json.loads(index_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    datasets = payload.get("datasets")
    if not isinstance(datasets, list):
        return []
    return [d for d in datasets if isinstance(d, dict) and "metric" in d]


def upsert_index_datasets(
    existing: list[dict[str, Any]],
    updates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_metric = {str(d["metric"]): d for d in existing}
    for item in updates:
        by_metric[str(item["metric"])] = item
    # Preserve prior order, append new metrics at end.
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for d in existing:
        m = str(d["metric"])
        ordered.append(by_metric[m])
        seen.add(m)
    for item in updates:
        m = str(item["metric"])
        if m not in seen:
            ordered.append(item)
            seen.add(m)
    return ordered


def convert_balance_metric(
    metric: str,
    csv_path: Path,
    json_path: Path,
) -> dict[str, Any] | None:
    if not csv_path.exists():
        print(f"[warn] balance csv not found, skip {metric}: {csv_path}")
        return None
    incoming = normalize_wide_df(read_csv_with_fallback(csv_path))
    merged, stats = merge_wide_prefer_csv(load_existing_json_df(json_path), incoming)
    payload = {
        "code": 0,
        "msg": "success",
        "data": build_payload(metric, merged_source_label(csv_path.name), merged),
    }
    write_json(json_path, payload)
    print(
        f"[merge] {metric}: csv",
        stats.get("from_csv", 0),
        "json",
        stats.get("from_json", 0),
    )
    print(f"  {json_path.name}: {payload['data']['row_count']} rows")
    return {
        "metric": metric,
        "file": json_path.name,
        "source_csv": merged_source_label(csv_path.name),
        "rows": payload["data"]["row_count"],
    }


def convert_reserve_csvs(*, asset_csv: Path, index_json: Path) -> None:
    """Convert DefiLlama USD assets + coin-balance CSVs; leave volume JSON untouched."""
    asset_json = OUTPUT_JSON_DIR / "cex_exchange_daily_assets_usd.json"
    asset_new = normalize_wide_df(read_csv_with_fallback(asset_csv))
    asset_merged, asset_stats = merge_wide_prefer_csv(load_existing_json_df(asset_json), asset_new)
    asset_payload = {
        "code": 0,
        "msg": "success",
        "data": build_payload(
            "cex_exchange_daily_assets_usd",
            merged_source_label(asset_csv.name),
            asset_merged,
        ),
    }
    write_json(asset_json, asset_payload)
    print("[merge] assets: csv", asset_stats.get("from_csv", 0), "json", asset_stats.get("from_json", 0))
    print(f"  {asset_json.name}: {asset_payload['data']['row_count']} rows")

    index_updates: list[dict[str, Any]] = [
        {
            "metric": "cex_exchange_daily_assets_usd",
            "file": asset_json.name,
            "source_csv": merged_source_label(asset_csv.name),
            "rows": asset_payload["data"]["row_count"],
        }
    ]
    for metric, csv_path in BALANCE_METRICS:
        entry = convert_balance_metric(metric, csv_path, OUTPUT_JSON_DIR / f"{metric}.json")
        if entry is not None:
            index_updates.append(entry)

    datasets = upsert_index_datasets(load_index_datasets(index_json), index_updates)
    write_json(
        index_json,
        {
            "datasets": datasets,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    print(f"DONE (--skip-volume). json -> {OUTPUT_JSON_DIR}")


def main() -> None:
    ensure_cache_layout()
    args = parse_args()
    pipeline_xlsx = Path(args.pipeline_xlsx)
    asset_csv = Path(args.asset_csv)

    if not asset_csv.exists():
        raise SystemExit(f"asset csv not found: {asset_csv}")

    spot_volume_json = OUTPUT_JSON_DIR / "cex_exchange_daily_spot_volume_usd.json"
    futures_volume_json = OUTPUT_JSON_DIR / "cex_exchange_daily_futures_volume_usd.json"
    spot_monthly_json = OUTPUT_JSON_DIR / "cex_exchange_monthly_spot_volume_usd.json"
    futures_monthly_json = OUTPUT_JSON_DIR / "cex_exchange_monthly_futures_volume_usd.json"
    asset_json = OUTPUT_JSON_DIR / "cex_exchange_daily_assets_usd.json"
    ratio_json = OUTPUT_JSON_DIR / "cex_exchange_daily_futures_spot_ratio.json"
    index_json = OUTPUT_JSON_DIR / "index.json"

    if args.skip_volume:
        convert_reserve_csvs(asset_csv=asset_csv, index_json=index_json)
        return

    spot_new = build_daily_volume_usd_from_xlsx(pipeline_xlsx, "现货usd调整")
    futures_new = build_daily_volume_usd_from_xlsx(pipeline_xlsx, "合约usd调整")
    asset_new = normalize_wide_df(read_csv_with_fallback(asset_csv))
    closed_months = list_closed_months()
    if closed_months:
        print(f"[freeze] closed months for JSON merge: {', '.join(sorted(closed_months))}")

    try:
        spot_monthly_new = build_monthly_volume_from_xlsx(pipeline_xlsx, SPOT_MONTHLY_SHEET)
    except ValueError as exc:
        raise SystemExit(f"monthly sheet missing in {pipeline_xlsx.name}: {SPOT_MONTHLY_SHEET} ({exc})") from exc
    try:
        futures_monthly_new = build_monthly_volume_from_xlsx(pipeline_xlsx, FUTURES_MONTHLY_SHEET)
    except ValueError as exc:
        raise SystemExit(f"monthly sheet missing in {pipeline_xlsx.name}: {FUTURES_MONTHLY_SHEET} ({exc})") from exc

    spot_monthly_new = filter_monthly_by_daily_valid_days(spot_monthly_new, spot_new)
    futures_monthly_new = filter_monthly_by_daily_valid_days(futures_monthly_new, futures_new)

    spot_merged, spot_stats = merge_wide_prefer_csv(
        load_existing_json_df(spot_volume_json),
        spot_new,
        freeze_closed=True,
        closed_months=closed_months,
    )
    futures_merged, fut_stats = merge_wide_prefer_csv(
        load_existing_json_df(futures_volume_json),
        futures_new,
        freeze_closed=True,
        closed_months=closed_months,
    )
    asset_merged, asset_stats = merge_wide_prefer_csv(load_existing_json_df(asset_json), asset_new)
    spot_monthly_merged, spot_mo_stats = merge_wide_prefer_csv(
        load_existing_json_df(spot_monthly_json, period_col="month"),
        spot_monthly_new,
        period_col="month",
        freeze_closed=True,
        closed_months=closed_months,
    )
    futures_monthly_merged, fut_mo_stats = merge_wide_prefer_csv(
        load_existing_json_df(futures_monthly_json, period_col="month"),
        futures_monthly_new,
        period_col="month",
        freeze_closed=True,
        closed_months=closed_months,
    )

    spot_vol_df = append_total_column(spot_merged)
    futures_vol_df = append_total_column(futures_merged)
    # 用 xlsx 日度（非 merge 后日度）判断完整性，避免旧 JSON 补全日导致误判
    spot_monthly_merged = filter_monthly_by_daily_valid_days(
        spot_monthly_merged, spot_new, period_col="month"
    )
    futures_monthly_merged = filter_monthly_by_daily_valid_days(
        futures_monthly_merged, futures_new, period_col="month"
    )
    spot_monthly_df = append_total_column(spot_monthly_merged, period_col="month")
    futures_monthly_df = append_total_column(futures_monthly_merged, period_col="month")
    ratio_new = build_futures_spot_ratio_df(spot_vol_df, futures_vol_df)
    ratio_merged, ratio_stats = merge_wide_prefer_csv(
        load_existing_json_df(ratio_json),
        ratio_new,
        freeze_closed=True,
        closed_months=closed_months,
    )
    asset_df = asset_merged

    print(
        "[merge] spot daily: csv",
        spot_stats.get("from_csv", 0),
        "json",
        spot_stats.get("from_json", 0),
        "frozen",
        spot_stats.get("frozen", 0),
    )
    print(
        "[merge] futures daily: csv",
        fut_stats.get("from_csv", 0),
        "json",
        fut_stats.get("from_json", 0),
        "frozen",
        fut_stats.get("frozen", 0),
    )
    print(
        "[merge] spot monthly: csv",
        spot_mo_stats.get("from_csv", 0),
        "json",
        spot_mo_stats.get("from_json", 0),
        "frozen",
        spot_mo_stats.get("frozen", 0),
    )
    print(
        "[merge] futures monthly: csv",
        fut_mo_stats.get("from_csv", 0),
        "json",
        fut_mo_stats.get("from_json", 0),
        "frozen",
        fut_mo_stats.get("frozen", 0),
    )
    print("[merge] assets: csv", asset_stats.get("from_csv", 0), "json", asset_stats.get("from_json", 0))
    print("[merge] ratio: csv", ratio_stats.get("from_csv", 0), "json", ratio_stats.get("from_json", 0))

    spot_volume_payload = {
        "code": 0,
        "msg": "success",
        "data": build_payload(
            "cex_exchange_daily_spot_volume_usd",
            merged_source_label(pipeline_xlsx.name),
            spot_vol_df,
        ),
    }
    futures_volume_payload = {
        "code": 0,
        "msg": "success",
        "data": build_payload(
            "cex_exchange_daily_futures_volume_usd",
            merged_source_label(pipeline_xlsx.name),
            futures_vol_df,
        ),
    }
    asset_payload = {
        "code": 0,
        "msg": "success",
        "data": build_payload(
            "cex_exchange_daily_assets_usd",
            merged_source_label(asset_csv.name),
            asset_df,
        ),
    }
    ratio_payload = {
        "code": 0,
        "msg": "success",
        "data": build_payload(
            "cex_exchange_daily_futures_spot_ratio",
            merged_source_label(pipeline_xlsx.name),
            ratio_merged,
        ),
    }
    spot_monthly_payload = {
        "code": 0,
        "msg": "success",
        "data": build_payload(
            "cex_exchange_monthly_spot_volume_usd",
            merged_source_label(pipeline_xlsx.name),
            spot_monthly_df,
            period_col="month",
        ),
    }
    futures_monthly_payload = {
        "code": 0,
        "msg": "success",
        "data": build_payload(
            "cex_exchange_monthly_futures_volume_usd",
            merged_source_label(pipeline_xlsx.name),
            futures_monthly_df,
            period_col="month",
        ),
    }

    write_json(spot_volume_json, spot_volume_payload)
    write_json(futures_volume_json, futures_volume_payload)
    write_json(spot_monthly_json, spot_monthly_payload)
    write_json(futures_monthly_json, futures_monthly_payload)
    write_json(asset_json, asset_payload)
    write_json(ratio_json, ratio_payload)

    index_datasets: list[dict[str, Any]] = [
        {
            "metric": "cex_exchange_daily_spot_volume_usd",
            "file": spot_volume_json.name,
            "source_csv": merged_source_label(pipeline_xlsx.name),
            "rows": spot_volume_payload["data"]["row_count"],
        },
        {
            "metric": "cex_exchange_daily_futures_volume_usd",
            "file": futures_volume_json.name,
            "source_csv": merged_source_label(pipeline_xlsx.name),
            "rows": futures_volume_payload["data"]["row_count"],
        },
        {
            "metric": "cex_exchange_monthly_spot_volume_usd",
            "file": spot_monthly_json.name,
            "source_csv": merged_source_label(pipeline_xlsx.name),
            "rows": spot_monthly_payload["data"]["row_count"],
        },
        {
            "metric": "cex_exchange_monthly_futures_volume_usd",
            "file": futures_monthly_json.name,
            "source_csv": merged_source_label(pipeline_xlsx.name),
            "rows": futures_monthly_payload["data"]["row_count"],
        },
        {
            "metric": "cex_exchange_daily_assets_usd",
            "file": asset_json.name,
            "source_csv": merged_source_label(asset_csv.name),
            "rows": asset_payload["data"]["row_count"],
        },
        {
            "metric": "cex_exchange_daily_futures_spot_ratio",
            "file": ratio_json.name,
            "source_csv": merged_source_label(pipeline_xlsx.name),
            "rows": ratio_payload["data"]["row_count"],
        },
    ]
    for metric, csv_path in BALANCE_METRICS:
        entry = convert_balance_metric(metric, csv_path, OUTPUT_JSON_DIR / f"{metric}.json")
        if entry is not None:
            index_datasets.append(entry)

    write_json(
        index_json,
        {
            "datasets": index_datasets,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )

    print(f"DONE. json -> {OUTPUT_JSON_DIR}")
    print(f"  {spot_volume_json.name}: {spot_volume_payload['data']['row_count']} rows")
    print(f"  {futures_volume_json.name}: {futures_volume_payload['data']['row_count']} rows")
    print(f"  {spot_monthly_json.name}: {spot_monthly_payload['data']['row_count']} rows")
    print(f"  {futures_monthly_json.name}: {futures_monthly_payload['data']['row_count']} rows")
    print(f"  {asset_json.name}: {asset_payload['data']['row_count']} rows")
    print(f"  {ratio_json.name}: {ratio_payload['data']['row_count']} rows")


if __name__ == "__main__":
    main()

