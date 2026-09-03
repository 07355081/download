"""Build CEX monthly report xlsx from traffic PDFs + pipeline volume xlsx.

Data sources:
  - Volume current month: VPS cache/cex_volume_pipeline_unified.xlsx
    (现货月度 / 合约月度); fetched on demand, skipped if local size matches
  - Volume / traffic previous month: monthly_report_{prev}.xlsx (bootstrap: sample.xlsx)
  - Traffic current month: SimilarWeb PDFs in traffic/ (local)

Previous-month values are taken from the prior monthly report (not fresh pipeline)
so MoM figures stay consistent across published reports. Terminal output shows how
much the latest pipeline differs from that frozen prior report.

Each run writes monthly_report_YYYY-MM.xlsx (never overwrites; use --force).
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
from datetime import datetime
from pathlib import Path

import pandas as pd
import pdfplumber

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent
SAMPLE_XLSX = ROOT / "sample.xlsx"
CACHE_DIR = BASE / "cache"
PIPELINE_XLSX = CACHE_DIR / "cex_volume_pipeline_unified.xlsx"
LEGACY_PIPELINE_XLSX = BASE / "cex_volume_pipeline_unified.xlsx"
SPOT_MONTHLY_SHEET = "现货月度"
FUTURES_MONTHLY_SHEET = "合约月度"
TRAFFIC_DIR = ROOT / "traffic"

# VPS pull (bandwidth-aware: single file + size check, never sync whole tree)
DEFAULT_VPS_HOST = os.environ.get("CEX_VPS_HOST", "root@47.74.6.207")
DEFAULT_VPS_KEY = Path(
    os.environ.get("CEX_VPS_SSH_KEY", str(Path.home() / ".ssh" / "data-dashboard-prod"))
)
REMOTE_CEX_DIR = "/root/data-download/cex-asset&vol"
REMOTE_PIPELINE = f"{REMOTE_CEX_DIR}/cache/cex_volume_pipeline_unified.xlsx"

# Exchange universe (from sample.xlsx)
SPOT_EXCHANGES = [
    "Binance", "OKX", "Bybit", "Coinbase", "Uniswap", "Kraken", "Bitget", "HTX",
    "MEXC", "Gate", "BitMart", "Upbit", "KuCoin", "Bitfinex", "Crypto.com",
]
FUTURES_EXCHANGES = [
    "Binance", "OKX", "Bybit", "Coinbase", "Hyperliquid", "Bitget", "Gate", "HTX",
    "BitMart", "Deribit", "Kraken", "KuCoin", "Crypto.com",
]
TRAFFIC_EXCHANGES = [
    "Binance", "OKX", "Coinbase", "KuCoin", "Bybit", "Bitget", "Kraken", "Upbit",
    "Crypto.com", "HTX", "Deribit", "Bitfinex",
]

PDF_EXCHANGE = {
    "binance": "Binance",
    "bitfinex": "Bitfinex",
    "bitget": "Bitget",
    "bybit": "Bybit",
    "coinbase": "Coinbase",
    "crypto": "Crypto.com",
    "deribit": "Deribit",
    "htx": "HTX",
    "kraken": "Kraken",
    "kucoin": "KuCoin",
    "okx": "OKX",
    "upbit": "Upbit",
}

COUNTRY_ALIASES = {
    "Republic of Korea": "Korea",
    "South Korea": "Korea",
    "United States": "USA",
    "United Kingdom": "UK",
    "Russian Federation": "Russia",
    "Türkiye": "Turkey",
    "Hong Kong SAR China": "Hong Kong",
    "Hong Kong SAR": "Hong Kong",
}

MONTH_NAME_TO_NUM = {
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}

MONTH_PATTERN = (
    r"(January|February|March|April|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})"
)


def ssh_base(host: str, key: Path) -> list[str]:
    return [
        "ssh",
        "-i",
        str(key),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=20",
        "-o",
        "ServerAliveInterval=15",
        host,
    ]


def remote_file_size(host: str, key: Path, remote_path: str) -> int:
    """Stat remote file size only (tiny SSH round-trip; no file transfer)."""
    # Quote carefully for remote shell; path may contain &.
    remote_cmd = f"stat -c %s -- {shlex.quote(remote_path)}"
    proc = subprocess.run(
        ssh_base(host, key) + [remote_cmd],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise FileNotFoundError(f"remote stat failed for {remote_path}: {err}")
    return int(proc.stdout.strip())


def fetch_pipeline_from_vps(
    *,
    host: str,
    key: Path,
    dest: Path,
    force: bool = False,
) -> Path:
    """Download only the pipeline xlsx from VPS; skip when local size matches."""
    if not key.is_file():
        raise FileNotFoundError(f"SSH key not found: {key}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    remote_size = remote_file_size(host, key, REMOTE_PIPELINE)
    if dest.is_file() and not force and dest.stat().st_size == remote_size:
        print(
            f"[vps] skip download (local size matches remote "
            f"{remote_size:,} bytes): {dest.name}"
        )
        return dest

    print(
        f"[vps] downloading {REMOTE_PIPELINE} "
        f"({remote_size:,} bytes) → {dest}"
    )
    # scp one file only; do not rsync the whole module tree.
    tmp = dest.with_suffix(dest.suffix + ".partial")
    if tmp.exists():
        tmp.unlink()
    scp_cmd = [
        "scp",
        "-i",
        str(key),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=20",
        f"{host}:{REMOTE_PIPELINE}",
        str(tmp),
    ]
    subprocess.run(scp_cmd, check=True)
    got = tmp.stat().st_size
    if got != remote_size:
        tmp.unlink(missing_ok=True)
        raise IOError(f"download size mismatch: got {got}, expected {remote_size}")
    tmp.replace(dest)
    print(f"[vps] saved {dest} ({got:,} bytes)")
    return dest


def resolve_pipeline_xlsx() -> Path:
    if PIPELINE_XLSX.is_file():
        return PIPELINE_XLSX
    if LEGACY_PIPELINE_XLSX.is_file():
        return LEGACY_PIPELINE_XLSX
    raise FileNotFoundError(
        f"{PIPELINE_XLSX} not found; fetch from VPS or place xlsx under cache/"
    )


def load_pipeline_monthly(
    sheet: str, month: pd.Timestamp, pipeline_xlsx: Path
) -> dict[str, float]:
    if not pipeline_xlsx.is_file():
        raise FileNotFoundError(f"{pipeline_xlsx} not found")

    df = pd.read_excel(pipeline_xlsx, sheet_name=sheet)
    ex_col = df.columns[0]
    month_key = month.strftime("%Y-%m")
    col = next((c for c in df.columns if str(c) == month_key), None)
    if col is None:
        available = [str(c) for c in df.columns if c != ex_col]
        raise KeyError(f"{sheet} missing {month_key}; available: {', '.join(available)}")

    values: dict[str, float] = {}
    for _, row in df.iterrows():
        ex = row[ex_col]
        if pd.isna(ex):
            continue
        val = row[col]
        if pd.notna(val):
            values[str(ex)] = float(val)
    return values


def month_start(year: int, month: int) -> pd.Timestamp:
    return pd.Timestamp(year=year, month=month, day=1)


def parse_visit_millions(raw: str) -> float:
    s = raw.strip().replace(",", "")
    if s.endswith("M"):
        return float(s[:-1])
    if s.endswith("K"):
        return float(s[:-1]) / 1000
    val = float(s)
    return val / 1_000_000 if val >= 1000 else val


def normalize_country(name: str) -> str:
    name = name.strip()
    return COUNTRY_ALIASES.get(name, name)


def parse_traffic_pdf(path: Path) -> dict:
    with pdfplumber.open(path) as doc:
        cover = doc.pages[0].extract_text() or ""
        overview = doc.pages[1].extract_text() or ""
        geo = doc.pages[3].extract_text() if len(doc.pages) > 3 else ""

    month_match = re.search(MONTH_PATTERN, cover)
    if not month_match:
        raise ValueError(f"month not found in {path.name}")
    month_name, year = month_match.group(1), int(month_match.group(2))
    month_num = MONTH_NAME_TO_NUM[month_name]

    visit_match = re.search(r"Monthly visits\s+([\d,.]+[KMB]?)", overview)
    if not visit_match:
        raise ValueError(f"monthly visits not found in {path.name}")
    visits = round(parse_visit_millions(visit_match.group(1)), 2)

    countries: list[tuple[str, float]] = []
    for line in geo.splitlines():
        line = line.strip()
        if not line or line.startswith(("Geography", "Country")) or "SimilarWeb" in line:
            continue
        m = re.match(r"^(.+?)\s+([\d.]+)%", line)
        if m:
            countries.append(
                (normalize_country(m.group(1)), round(float(m.group(2)) / 100, 4))
            )
        if len(countries) >= 3:
            break

    stem = path.stem.lower()
    exchange = PDF_EXCHANGE.get(stem)
    if exchange is None:
        raise ValueError(f"unknown exchange pdf name: {path.name}")

    return {
        "exchange": exchange,
        "year": year,
        "month": month_num,
        "visits_m": visits,
        "countries": countries,
    }


def load_traffic_from_pdfs() -> tuple[pd.Timestamp, dict[str, dict]]:
    records = [parse_traffic_pdf(p) for p in sorted(TRAFFIC_DIR.glob("*.pdf"))]
    if not records:
        raise FileNotFoundError(f"no PDFs in {TRAFFIC_DIR}")

    months = {(r["year"], r["month"]) for r in records}
    if len(months) != 1:
        raise ValueError(f"traffic PDFs span multiple months: {sorted(months)}")
    year, month = next(iter(months))
    curr_month = month_start(year, month)

    by_exchange = {r["exchange"]: r for r in records}
    return curr_month, by_exchange


def datetime_value_columns(df: pd.DataFrame) -> list:
    cols = []
    for col in df.columns:
        if col in ("Exchange", "MoM %", "Geographic Location") or str(col).startswith("Unnamed"):
            continue
        if isinstance(col, (pd.Timestamp, datetime)):
            cols.append(col)
            continue
        try:
            cols.append(pd.Timestamp(col))
        except (ValueError, TypeError):
            continue
    return sorted({pd.Timestamp(c) for c in cols})


def resolve_prev_source(curr_month: pd.Timestamp, explicit: Path | None) -> Path:
    if explicit is not None:
        path = explicit if explicit.is_absolute() else ROOT / explicit
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    prev_month = curr_month - pd.offsets.MonthBegin(1)
    report_path = ROOT / f"monthly_report_{prev_month:%Y-%m}.xlsx"
    if report_path.is_file():
        return report_path

    if SAMPLE_XLSX.is_file():
        print(
            f"[INFO] {report_path.name} not found; "
            f"using sample.xlsx for {prev_month:%Y-%m} (bootstrap only)"
        )
        return SAMPLE_XLSX

    raise FileNotFoundError(
        f"previous report not found: {report_path}\n"
        f"add monthly_report_{prev_month:%Y-%m}.xlsx or keep sample.xlsx for first run"
    )


def load_prev_sheet(
    path: Path, sheet: str, target_month: pd.Timestamp
) -> tuple[list[str], dict[str, float]]:
    df = pd.read_excel(path, sheet_name=sheet)
    dt_cols = datetime_value_columns(df)
    if not dt_cols:
        raise ValueError(f"no month columns in {path.name} [{sheet}]")

    target = pd.Timestamp(target_month)
    matched = next((c for c in dt_cols if pd.Timestamp(c).normalize() == target.normalize()), None)
    if matched is None:
        raise KeyError(
            f"{path.name} [{sheet}] has no column for {target:%Y-%m}; "
            f"available: {', '.join(c.strftime('%Y-%m') for c in dt_cols)}"
        )

    col_map = {}
    for col in df.columns:
        if col in ("Exchange", "MoM %", "Geographic Location") or str(col).startswith("Unnamed"):
            continue
        try:
            col_map[pd.Timestamp(col).normalize()] = col
        except (ValueError, TypeError):
            continue
    target_raw = col_map[matched.normalize()]
    exchanges = [str(x) for x in df["Exchange"].tolist() if pd.notna(x) and str(x) != "SUM"]
    values: dict[str, float] = {}
    for _, row in df.iterrows():
        ex = row["Exchange"]
        if pd.isna(ex) or str(ex) == "SUM":
            continue
        val = row[target_raw]
        if pd.notna(val):
            values[str(ex)] = float(val)
    return exchanges, values


def sort_by_curr_month(exchanges: list[str], curr_values: dict[str, float]) -> list[str]:
    return sorted(exchanges, key=lambda ex: curr_values.get(ex, float("-inf")), reverse=True)


def pick_values(values: dict[str, float], allowed: list[str]) -> dict[str, float]:
    allowed_set = set(allowed)
    return {k: v for k, v in values.items() if k in allowed_set}


def sum_picked(values: dict[str, float], exchanges: list[str]) -> float:
    return sum(float(values[ex]) for ex in exchanges if ex in values)


def rel_pct_diff(frozen: float, fresh: float) -> float | None:
    if frozen == 0 or pd.isna(frozen) or pd.isna(fresh):
        return None
    return (fresh - frozen) / frozen


def print_prev_month_drift(
    sheet_label: str,
    prev_month: pd.Timestamp,
    exchanges: list[str],
    report_vals: dict[str, float],
    pipeline_vals: dict[str, float],
    *,
    prev_path: Path,
) -> None:
    """Print how much fresh pipeline prev-month data diverges from prior report."""
    report_sum = sum_picked(report_vals, exchanges)
    pipeline_sum = sum_picked(pipeline_vals, exchanges)
    sum_diff = rel_pct_diff(report_sum, pipeline_sum)

    print(f"\n[上月偏差] {prev_month:%Y-%m} {sheet_label} (沿用上份月报 {prev_path.name})")
    if sum_diff is None:
        print("  SUM: 无法计算（分母为 0 或缺失）")
    else:
        sign = "+" if sum_diff >= 0 else ""
        print(
            f"  SUM: 月报={report_sum:,.0f}  pipeline={pipeline_sum:,.0f}  "
            f"偏差={sign}{sum_diff * 100:.2f}%"
        )

    rows: list[tuple[str, float, float, float]] = []
    for ex in exchanges:
        frozen = report_vals.get(ex)
        fresh = pipeline_vals.get(ex)
        if frozen is None or fresh is None or pd.isna(frozen) or pd.isna(fresh):
            continue
        diff = rel_pct_diff(float(frozen), float(fresh))
        if diff is None or diff == 0:
            continue
        rows.append((ex, float(frozen), float(fresh), diff))

    if not rows:
        print("  各交易所: 与 pipeline 一致（或无可比项）")
        return

    rows.sort(key=lambda r: abs(r[3]), reverse=True)
    print("  各交易所 (pipeline 相对月报):")
    for ex, frozen, fresh, diff in rows:
        sign = "+" if diff >= 0 else ""
        print(f"    {ex}: 月报={frozen:,.0f}  pipeline={fresh:,.0f}  偏差={sign}{diff * 100:.2f}%")


def mom_pct(prev: float | None, curr: float | None, *, digits: int) -> float | None:
    if prev is None or curr is None or pd.isna(prev) or pd.isna(curr) or prev == 0:
        return None
    return round((curr - prev) / prev, digits)


def build_value_sheet(
    exchanges: list[str],
    prev_values: dict[str, float],
    curr_values: dict[str, float],
    prev_month: pd.Timestamp,
    curr_month: pd.Timestamp,
    *,
    as_int: bool,
    mom_digits: int,
) -> pd.DataFrame:
    rows = []
    prev_sum = 0.0
    curr_sum = 0.0
    for ex in exchanges:
        prev = prev_values.get(ex)
        curr = curr_values.get(ex)
        if prev is not None:
            prev_sum += prev
        if curr is not None:
            curr_sum += curr

        def fmt(v: float | None):
            if v is None:
                return None
            return int(v) if as_int else round(v, 2)

        rows.append(
            {
                "Exchange": ex,
                prev_month: fmt(prev),
                curr_month: fmt(curr),
                "MoM %": mom_pct(prev, curr, digits=mom_digits),
            }
        )
    rows.append(
        {
            "Exchange": "SUM",
            prev_month: fmt(prev_sum),
            curr_month: fmt(curr_sum),
            "MoM %": mom_pct(prev_sum, curr_sum, digits=mom_digits),
        }
    )
    return pd.DataFrame(rows)


def build_traffic_sheet(
    exchanges: list[str],
    prev_month: pd.Timestamp,
    curr_month: pd.Timestamp,
    prev_visits: dict[str, float],
    curr_pdf: dict[str, dict],
) -> pd.DataFrame:
    curr_visits = {ex: rec["visits_m"] for ex, rec in curr_pdf.items()}
    base = build_value_sheet(
        exchanges,
        prev_visits,
        curr_visits,
        prev_month,
        curr_month,
        as_int=False,
        mom_digits=4,
    )
    geo_cols = ["Geographic Location", "Unnamed: 5", "Unnamed: 6", "Unnamed: 7", "Unnamed: 8", "Unnamed: 9"]
    for col in geo_cols:
        base[col] = None

    for i, ex in enumerate(exchanges):
        countries = curr_pdf.get(ex, {}).get("countries", [])
        if len(countries) > 0:
            base.at[i, "Geographic Location"] = countries[0][0]
            base.at[i, "Unnamed: 5"] = countries[0][1]
        if len(countries) > 1:
            base.at[i, "Unnamed: 6"] = countries[1][0]
            base.at[i, "Unnamed: 7"] = countries[1][1]
        if len(countries) > 2:
            base.at[i, "Unnamed: 8"] = countries[2][0]
            base.at[i, "Unnamed: 9"] = countries[2][1]
    return base


def write_report(
    output: Path,
    spot_df: pd.DataFrame,
    futures_df: pd.DataFrame,
    traffic_df: pd.DataFrame | None,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        spot_df.to_excel(writer, sheet_name="spot", index=False)
        futures_df.to_excel(writer, sheet_name="futures", index=False)
        if traffic_df is not None:
            traffic_df.to_excel(writer, sheet_name="traffic", index=False)


def parse_month_arg(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return pd.Timestamp(year=ts.year, month=ts.month, day=1)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--month",
        type=str,
        default=None,
        help="report month YYYY-MM (required with --volume-only; else inferred from traffic PDFs)",
    )
    p.add_argument(
        "--volume-only",
        action="store_true",
        help="build spot/futures sheets only; skip traffic PDFs",
    )
    p.add_argument(
        "--prev",
        type=Path,
        default=None,
        help="previous monthly_report xlsx (default: monthly_report_{prev_month}.xlsx)",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="output xlsx path (default: monthly_report_YYYY-MM.xlsx)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="overwrite output if monthly_report_YYYY-MM.xlsx already exists",
    )
    p.add_argument(
        "--skip-fetch",
        action="store_true",
        help="do not contact VPS; use local cache/cex_volume_pipeline_unified.xlsx",
    )
    p.add_argument(
        "--force-fetch",
        action="store_true",
        help="re-download pipeline xlsx even if local size matches remote",
    )
    p.add_argument(
        "--vps-host",
        default=DEFAULT_VPS_HOST,
        help=f"SSH target (default: {DEFAULT_VPS_HOST})",
    )
    p.add_argument(
        "--vps-key",
        type=Path,
        default=DEFAULT_VPS_KEY,
        help=f"SSH private key (default: {DEFAULT_VPS_KEY})",
    )
    p.add_argument(
        "--pipeline",
        type=Path,
        default=None,
        help="local pipeline xlsx path (default: cache/cex_volume_pipeline_unified.xlsx)",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.volume_only and not args.month:
        raise SystemExit("--volume-only requires --month YYYY-MM")
    if not args.volume_only and not TRAFFIC_DIR.is_dir():
        raise FileNotFoundError(TRAFFIC_DIR)

    pipeline_xlsx = args.pipeline or PIPELINE_XLSX
    if not args.skip_fetch:
        fetch_pipeline_from_vps(
            host=args.vps_host,
            key=args.vps_key,
            dest=pipeline_xlsx,
            force=args.force_fetch,
        )
    else:
        pipeline_xlsx = args.pipeline or resolve_pipeline_xlsx()
        print(f"[vps] --skip-fetch; using local {pipeline_xlsx}")

    if args.volume_only:
        curr_month = parse_month_arg(args.month)
        traffic_pdf: dict[str, dict] = {}
    else:
        curr_month, traffic_pdf = load_traffic_from_pdfs()
        if args.month is not None:
            forced = parse_month_arg(args.month)
            if forced != curr_month:
                raise SystemExit(
                    f"--month {forced:%Y-%m} conflicts with traffic PDFs ({curr_month:%Y-%m})"
                )

    prev_month = curr_month - pd.offsets.MonthBegin(1)
    prev_path = resolve_prev_source(curr_month, args.prev)

    spot_prev_report = pick_values(load_prev_sheet(prev_path, "spot", prev_month)[1], SPOT_EXCHANGES)
    fut_prev_report = pick_values(load_prev_sheet(prev_path, "futures", prev_month)[1], FUTURES_EXCHANGES)
    spot_prev_pipeline = pick_values(
        load_pipeline_monthly(SPOT_MONTHLY_SHEET, prev_month, pipeline_xlsx), SPOT_EXCHANGES
    )
    fut_prev_pipeline = pick_values(
        load_pipeline_monthly(FUTURES_MONTHLY_SHEET, prev_month, pipeline_xlsx), FUTURES_EXCHANGES
    )

    print_prev_month_drift(
        "现货",
        prev_month,
        SPOT_EXCHANGES,
        spot_prev_report,
        spot_prev_pipeline,
        prev_path=prev_path,
    )
    print_prev_month_drift(
        "合约",
        prev_month,
        FUTURES_EXCHANGES,
        fut_prev_report,
        fut_prev_pipeline,
        prev_path=prev_path,
    )

    spot_prev = spot_prev_report
    spot_curr = pick_values(
        load_pipeline_monthly(SPOT_MONTHLY_SHEET, curr_month, pipeline_xlsx), SPOT_EXCHANGES
    )
    fut_prev = fut_prev_report
    fut_curr = pick_values(
        load_pipeline_monthly(FUTURES_MONTHLY_SHEET, curr_month, pipeline_xlsx), FUTURES_EXCHANGES
    )

    spot_df = build_value_sheet(
        sort_by_curr_month(SPOT_EXCHANGES, spot_curr),
        spot_prev,
        spot_curr,
        prev_month,
        curr_month,
        as_int=True,
        mom_digits=3,
    )
    futures_df = build_value_sheet(
        sort_by_curr_month(FUTURES_EXCHANGES, fut_curr),
        fut_prev,
        fut_curr,
        prev_month,
        curr_month,
        as_int=True,
        mom_digits=3,
    )

    traffic_df = None
    if not args.volume_only:
        traffic_prev = pick_values(
            load_prev_sheet(prev_path, "traffic", prev_month)[1], TRAFFIC_EXCHANGES
        )
        curr_visits = {
            ex: rec["visits_m"]
            for ex, rec in traffic_pdf.items()
            if ex in TRAFFIC_EXCHANGES
        }
        traffic_pdf = {ex: rec for ex, rec in traffic_pdf.items() if ex in TRAFFIC_EXCHANGES}
        traffic_df = build_traffic_sheet(
            sort_by_curr_month(TRAFFIC_EXCHANGES, curr_visits),
            prev_month,
            curr_month,
            traffic_prev,
            traffic_pdf,
        )

    output = args.output or ROOT / f"monthly_report_{curr_month:%Y-%m}.xlsx"
    if output.exists() and not args.force:
        raise SystemExit(f"{output.name} already exists; use --force to overwrite")
    write_report(output, spot_df, futures_df, traffic_df)
    print(f"\nwritten {output}")
    print(f"  volume curr: {pipeline_xlsx} (from VPS unless --skip-fetch)")
    print(f"  volume prev: {prev_path.name} ({prev_month:%Y-%m}, frozen)")
    print(f"  months: {prev_month:%Y-%m} -> {curr_month:%Y-%m}")
    if args.volume_only:
        print("  traffic: skipped (--volume-only)")
    else:
        print(f"  traffic PDF exchanges: {len(traffic_pdf)}")


if __name__ == "__main__":
    main()
