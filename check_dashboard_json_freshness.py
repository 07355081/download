#!/usr/bin/env python3
"""Scan data-dashboard public/json and report the latest date per JSON file.

Usage:
    python check_dashboard_json_freshness.py
    python check_dashboard_json_freshness.py --root C:\\code\\data-dashboard\\public\\json
    python check_dashboard_json_freshness.py --output freshness_report.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_JSON_ROOT = Path(r"C:\code\data-dashboard\public\json")
DEFAULT_OUTPUT = SCRIPT_DIR / "output" / "dashboard_json_freshness.csv"

DATE_KEYS = frozenset({"date", "time", "month", "t", "ts", "timestamp", "timestamp_utc", "datetime", "day"})
META_KEYS = frozenset({"generated_at", "fetched_at", "updated_at", "created_at"})

DAY_RE = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
MONTH_RE = re.compile(r"^(\d{4})[-/](\d{1,2})$")
ISO_DAY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report last data date for each dashboard JSON file")
    parser.add_argument("--root", type=Path, default=DEFAULT_JSON_ROOT, help="public/json root")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output CSV path")
    parser.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="Optional module-level summary CSV (default: <output_stem>_by_module.csv)",
    )
    return parser.parse_args()


def normalize_day(value: Any) -> date | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = float(value)
        if n <= 0:
            return None
        if n >= 1e12:
            n /= 1000.0
        if n >= 1e9:
            try:
                return datetime.fromtimestamp(n, tz=timezone.utc).date()
            except (OSError, OverflowError, ValueError):
                return None
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()

    text = str(value).strip()
    if not text:
        return None

    m = DAY_RE.match(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    m = MONTH_RE.match(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), 1)
        except ValueError:
            return None

    m = ISO_DAY_RE.match(text)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            return None

    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            cleaned = text.replace("Z", "")
            return datetime.strptime(cleaned[:19], fmt).date()
        except ValueError:
            continue
    return None


def normalize_month(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    m = MONTH_RE.match(text)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    day = normalize_day(value)
    return f"{day.year:04d}-{day.month:02d}" if day else None


class DateScan:
    __slots__ = ("days_min", "days_max", "day_count", "months", "meta_min", "meta_max", "field_hits")

    def __init__(self) -> None:
        self.days_min: date | None = None
        self.days_max: date | None = None
        self.day_count = 0
        self.months: set[str] = set()
        self.meta_min: date | None = None
        self.meta_max: date | None = None
        self.field_hits: dict[str, int] = {}

    @property
    def has_series(self) -> bool:
        return self.days_min is not None or bool(self.months)

    @property
    def has_meta(self) -> bool:
        return self.meta_min is not None

    def add_day(self, value: Any, field: str, *, meta: bool = False) -> None:
        day = normalize_day(value)
        if day is None:
            month = normalize_month(value)
            if month:
                self.months.add(month)
                self.field_hits[field] = self.field_hits.get(field, 0) + 1
            return
        if meta:
            self.meta_min = day if self.meta_min is None else min(self.meta_min, day)
            self.meta_max = day if self.meta_max is None else max(self.meta_max, day)
        else:
            self.days_min = day if self.days_min is None else min(self.days_min, day)
            self.days_max = day if self.days_max is None else max(self.days_max, day)
            self.day_count += 1
        self.field_hits[field] = self.field_hits.get(field, 0) + 1

    def add_pair_series(self, rows: list[Any], field: str) -> None:
        for row in rows:
            if isinstance(row, (list, tuple)) and row:
                self.add_day(row[0], field)

    def merge_months(self, values: list[Any], field: str) -> None:
        for value in values:
            month = normalize_month(value)
            if month:
                self.months.add(month)
                self.field_hits[field] = self.field_hits.get(field, 0) + 1


def scan_dict_rows(rows: list[Any], scan: DateScan) -> None:
    if not rows or not isinstance(rows[0], dict):
        return
    first = rows[0]
    if "date" in first:
        for row in rows:
            if isinstance(row, dict):
                scan.add_day(row.get("date"), "data.rows.date")
    elif "month" in first:
        scan.merge_months([row.get("month") for row in rows if isinstance(row, dict)], "data.rows.month")


def scan_payload(payload: Any) -> DateScan:
    scan = DateScan()
    if not isinstance(payload, dict):
        if isinstance(payload, list):
            if payload and isinstance(payload[0], (list, tuple)):
                scan.add_pair_series(payload, "[][0]")
            elif payload and isinstance(payload[0], dict):
                keys = DATE_KEYS & {str(k).lower() for k in payload[0].keys()}
                for row in payload:
                    if not isinstance(row, dict):
                        continue
                    for key in keys:
                        scan.add_day(row.get(key), f"[].{key}")
        return scan

    for mk in META_KEYS:
        if mk in payload:
            scan.add_day(payload[mk], mk, meta=True)

    meta = payload.get("meta")
    if isinstance(meta, dict):
        for mk in META_KEYS:
            if mk in meta:
                scan.add_day(meta[mk], f"meta.{mk}", meta=True)

    data = payload.get("data")
    if isinstance(data, dict):
        for mk in META_KEYS:
            if mk in data:
                scan.add_day(data[mk], f"data.{mk}", meta=True)

        rows = data.get("rows")
        if isinstance(rows, list):
            scan_dict_rows(rows, scan)

        time_list = data.get("time_list")
        if isinstance(time_list, list):
            for item in time_list:
                scan.add_day(item, "data.time_list")

        chart = data.get("chart")
        if isinstance(chart, dict):
            for key in ("holdings", "holding_value_in_usd"):
                series = chart.get(key)
                if isinstance(series, list):
                    scan.add_pair_series(series, f"data.chart.{key}")

    elif isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, (list, tuple)):
            scan.add_pair_series(data, "data[][0]")
        elif isinstance(first, dict):
            keys = DATE_KEYS & {str(k).lower() for k in first.keys()}
            for row in data:
                if not isinstance(row, dict):
                    continue
                for key in keys:
                    scan.add_day(row.get(key), f"data[].{key}")

    series = payload.get("series")
    if isinstance(series, list):
        for item in series:
            if isinstance(item, dict) and "t" in item:
                scan.add_day(item["t"], "series.t")

    if not scan.has_series:
        rows = payload.get("rows")
        if isinstance(rows, list):
            scan_dict_rows(rows, scan)

    return scan


def fmt_day(d: date | None) -> str:
    return d.isoformat() if d else ""


def analyze_file(path: Path, root: Path) -> dict[str, Any]:
    rel = path.relative_to(root).as_posix()
    parts = rel.split("/")
    module = parts[0] if parts else ""
    stat = path.stat()

    row: dict[str, Any] = {
        "relative_path": rel,
        "module": module,
        "file_name": path.name,
        "file_size_bytes": stat.st_size,
        "file_mtime_utc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "ok",
        "granularity": "",
        "first_date": "",
        "last_date": "",
        "first_month": "",
        "last_month": "",
        "date_point_count": 0,
        "date_field": "",
        "generated_at": "",
        "notes": "",
    }

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:  # noqa: BLE001
            row["status"] = "parse_error"
            row["notes"] = str(exc)
            return row
    except json.JSONDecodeError as exc:
        row["status"] = "parse_error"
        row["notes"] = str(exc)
        return row
    except OSError as exc:
        row["status"] = "read_error"
        row["notes"] = str(exc)
        return row

    scan = scan_payload(payload)

    if scan.days_min and scan.days_max:
        row["granularity"] = "daily"
        row["first_date"] = fmt_day(scan.days_min)
        row["last_date"] = fmt_day(scan.days_max)
        row["date_point_count"] = scan.day_count
    elif scan.months:
        ordered = sorted(scan.months)
        row["granularity"] = "monthly"
        row["first_month"] = ordered[0]
        row["last_month"] = ordered[-1]
        row["first_date"] = f"{ordered[0]}-01"
        row["last_date"] = f"{ordered[-1]}-01"
        row["date_point_count"] = len(scan.months)
    elif scan.has_meta:
        row["granularity"] = "metadata_only"
        row["first_date"] = fmt_day(scan.meta_min)
        row["last_date"] = fmt_day(scan.meta_max)
        row["date_point_count"] = 1
        row["notes"] = "no time series; dates from generated_at/fetched_at only"
    else:
        row["status"] = "no_dates"
        row["granularity"] = "none"
        row["notes"] = "static or catalog JSON without detectable dates"

    if scan.field_hits:
        row["date_field"] = "; ".join(
            f"{k}({v})" for k, v in sorted(scan.field_hits.items(), key=lambda x: (-x[1], x[0]))
        )

    if scan.meta_max:
        row["generated_at"] = fmt_day(scan.meta_max)

    return row


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "relative_path",
        "module",
        "file_name",
        "status",
        "granularity",
        "first_date",
        "last_date",
        "first_month",
        "last_month",
        "date_point_count",
        "date_field",
        "generated_at",
        "file_size_bytes",
        "file_mtime_utc",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_module_summary(path: Path, rows: list[dict[str, Any]]) -> None:
    by_module: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_module.setdefault(str(row["module"]), []).append(row)

    summary_rows: list[dict[str, Any]] = []
    for module in sorted(by_module):
        items = by_module[module]
        ok_items = [r for r in items if r.get("last_date")]
        summary_rows.append(
            {
                "module": module,
                "file_count": len(items),
                "files_with_dates": len(ok_items),
                "files_no_dates": sum(1 for r in items if r["status"] == "no_dates"),
                "files_parse_error": sum(1 for r in items if r["status"] == "parse_error"),
                "module_last_date": max((str(r["last_date"]) for r in ok_items), default=""),
                "module_oldest_last_date": min((str(r["last_date"]) for r in ok_items), default=""),
            }
        )

    fields = list(summary_rows[0].keys()) if summary_rows else ["module"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary_rows)


def main() -> None:
    args = parse_args()
    root: Path = args.root
    if not root.is_dir():
        raise SystemExit(f"JSON root not found: {root}")

    # 跳过 gen_dashboard_meta.py 产出的 _indexes/ 与 _manifest.json：
    # 它们是目录索引和数据契约，不是数据集，混进新鲜度报表只会制造噪音。
    files = sorted(
        p
        for p in root.rglob("*.json")
        if not p.name.startswith("_") and "_indexes" not in p.relative_to(root).parts
    )
    print(f"Scanning {len(files)} JSON files under {root}", flush=True)

    rows: list[dict[str, Any]] = []
    for i, path in enumerate(files, 1):
        rows.append(analyze_file(path, root))
        if i % 5000 == 0:
            print(f"  ... {i}/{len(files)}", flush=True)

    rows.sort(key=lambda r: (r["module"], r["relative_path"]))
    write_csv(args.output, rows)

    summary_path = args.summary or args.output.with_name(f"{args.output.stem}_by_module{args.output.suffix}")
    write_module_summary(summary_path, rows)

    ok = sum(1 for r in rows if r["status"] == "ok")
    no_dates = sum(1 for r in rows if r["status"] == "no_dates")
    errors = sum(1 for r in rows if r["status"] not in ("ok", "no_dates"))

    print(f"DONE. detail CSV -> {args.output}", flush=True)
    print(f"      summary CSV -> {summary_path}", flush=True)
    print(f"      ok={ok} no_dates={no_dates} errors={errors} total={len(rows)}", flush=True)


if __name__ == "__main__":
    main()
