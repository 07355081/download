"""Convert fundraising master CSV into dashboard JSON envelopes.

Input:
  cache/fundraising_data.csv

Output:
  output/json/fundraising_deals.json
  output/json/monthly_stats.json
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from scrape_rootdata import clean_investor_name, parse_date_column

HERE = Path(__file__).resolve().parent
CACHE_CSV = HERE / "cache" / "fundraising_data.csv"
DEALS_JSON = HERE / "output" / "json" / "fundraising_deals.json"
MONTHLY_JSON = HERE / "output" / "json" / "monthly_stats.json"

EXCHANGE_RATES = {
    "USD": 1.0,
    "JPY": 0.0067,
    "CNY": 0.14,
    "EUR": 1.08,
    "GBP": 1.27,
    "KRW": 0.00075,
    "SGD": 0.74,
    "HKD": 0.13,
    "AUD": 0.66,
    "CAD": 0.72,
}


def to_usd(row: pd.Series) -> float:
    amount = row.get("amount")
    if pd.isna(amount) or amount <= 0:
        return 0.0
    currency = row.get("amount_currency")
    if pd.isna(currency) or str(currency).strip() == "":
        return float(amount)
    return float(amount) * EXCHANGE_RATES.get(str(currency).upper(), 1.0)


def pct_change_ratio(current: float, previous: float) -> float | None:
    if previous is None or (isinstance(previous, float) and math.isnan(previous)):
        return None
    if previous == 0 or pd.isna(current):
        return None
    return (float(current) - float(previous)) / float(previous)


def json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            return json_safe(value.item())
        except (ValueError, AttributeError):
            pass
    return value


def clean_investors_field(raw: Any) -> str | None:
    if raw is None or (isinstance(raw, float) and math.isnan(raw)):
        return None
    text = str(raw).strip()
    if not text or text.lower() == "nan":
        return None
    parts = [clean_investor_name(p.strip()) for p in text.split(",") if p.strip()]
    parts = [p for p in parts if p]
    return ", ".join(parts) if parts else None


def deals_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        item: dict[str, Any] = {}
        for col in df.columns:
            val = json_safe(row.get(col))
            if val is None:
                continue
            if col == "investors":
                cleaned = clean_investors_field(val)
                if cleaned is None:
                    continue
                item[col] = cleaned
            elif col in ("amount", "valuation", "investor_count"):
                try:
                    item[col] = float(val) if col != "investor_count" else int(float(val))
                except (TypeError, ValueError):
                    item[col] = val
            else:
                item[col] = val
        rows.append(item)
    return rows


def build_monthly_stats(df: pd.DataFrame) -> list[dict[str, Any]]:
    work = df.copy()
    work["date"] = parse_date_column(work["date"])
    work = work.dropna(subset=["date"]).copy()
    work["amount"] = pd.to_numeric(work["amount"], errors="coerce")
    work["amount_usd"] = work.apply(to_usd, axis=1)
    work["month_dt"] = work["date"].dt.to_period("M").dt.to_timestamp()

    grouped = (
        work.groupby("month_dt", as_index=False)
        .agg(
            deal_count=("date", "count"),
            amount_usd=("amount_usd", lambda s: float(s[s > 0].sum())),
        )
        .sort_values("month_dt")
    )

    count_by_month = grouped.set_index("month_dt")["deal_count"]
    amount_by_month = grouped.set_index("month_dt")["amount_usd"]

    out: list[dict[str, Any]] = []
    for _, row in grouped.iterrows():
        m = row["month_dt"]
        out.append(
            {
                "time": f"{m.year}-{m.month:02d}",
                "deal_count": int(row["deal_count"]),
                "amount_usd": float(row["amount_usd"]),
                "count_mom": pct_change_ratio(
                    row["deal_count"],
                    count_by_month.get(m - pd.DateOffset(months=1), float("nan")),
                ),
                "count_yoy": pct_change_ratio(
                    row["deal_count"],
                    count_by_month.get(m - pd.DateOffset(years=1), float("nan")),
                ),
                "amount_mom": pct_change_ratio(
                    row["amount_usd"],
                    amount_by_month.get(m - pd.DateOffset(months=1), float("nan")),
                ),
                "amount_yoy": pct_change_ratio(
                    row["amount_usd"],
                    amount_by_month.get(m - pd.DateOffset(years=1), float("nan")),
                ),
            }
        )
    return out


def write_json(path: Path, payload: dict[str, Any], *, compact: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    path.write_text(text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv-in", default=str(CACHE_CSV))
    parser.add_argument("--deals-out", default=str(DEALS_JSON))
    parser.add_argument("--monthly-out", default=str(MONTHLY_JSON))
    parser.add_argument("--compact", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    csv_in = Path(args.csv_in).resolve()
    if not csv_in.is_file():
        raise SystemExit(f"CSV not found: {csv_in}")

    df = pd.read_csv(csv_in, encoding="utf-8-sig")
    deals = deals_rows(df)
    monthly = build_monthly_stats(df)

    deals_payload = {"code": "0", "msg": "success", "data": deals}
    monthly_payload = {"code": "0", "msg": "success", "data": monthly}

    deals_out = Path(args.deals_out).resolve()
    monthly_out = Path(args.monthly_out).resolve()
    write_json(deals_out, deals_payload, compact=args.compact)
    write_json(monthly_out, monthly_payload, compact=args.compact)
    print(f"wrote {deals_out} rows={len(deals)}")
    print(f"wrote {monthly_out} rows={len(monthly)}")


if __name__ == "__main__":
    main()
