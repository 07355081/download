# -*- coding: utf-8 -*-
"""Closed-month helpers for volume freeze (monthly_report_YYYY-MM.xlsx present)."""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
MONTHLY_REPORT_DIR = BASE_DIR / "monthly-report"
REPORT_NAME_RE = re.compile(r"^monthly_report_(\d{4}-\d{2})\.xlsx$", re.I)


def list_closed_months(report_dir: Path | None = None) -> set[str]:
    """Return YYYY-MM set for formal monthly reports (excludes sample.xlsx)."""
    root = report_dir or MONTHLY_REPORT_DIR
    closed: set[str] = set()
    if not root.is_dir():
        return closed
    for path in root.iterdir():
        if not path.is_file():
            continue
        m = REPORT_NAME_RE.match(path.name)
        if m:
            closed.add(m.group(1))
    return closed


def period_is_closed(period: str | pd.Period | pd.Timestamp, closed: set[str]) -> bool:
    if isinstance(period, pd.Period):
        key = f"{period.year:04d}-{period.month:02d}"
    elif isinstance(period, pd.Timestamp):
        key = f"{period.year:04d}-{period.month:02d}"
    else:
        s = str(period).strip()
        key = s[:7] if len(s) >= 7 else s
    return key in closed


def date_in_closed_month(date_str: str, closed: set[str]) -> bool:
    try:
        ts = pd.Timestamp(date_str)
    except (ValueError, TypeError):
        return False
    if pd.isna(ts):
        return False
    return period_is_closed(ts, closed)
