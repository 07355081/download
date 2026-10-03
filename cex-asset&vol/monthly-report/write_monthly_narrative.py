"""Generate CN/EN monthly narratives from monthly_report_YYYY-MM.xlsx.

Writes up to three articles per language:
  - spot: spot volume MoM, share, ranking
  - futures: derivatives volume MoM, share, ranking, futures/spot ratio
  - traffic: website visits MoM (if traffic sheet present and non-empty)
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent

DASHBOARD_VOLUME_URL = "https://data.wublockchain.xyz/dashboard/exchange_volume"
DASHBOARD_TRAFFIC_URL = "https://data.wublock123.com/dashboard/website_traffic"

FOOTNOTE_VOLUME_CN = (
    "注：数据可能存在严重的刷量/机器人嫌疑，相关数据已经过预处理，"
    "包括异常值剔除、口径校准与标准化处理。"
)
FOOTNOTE_VOLUME_EN = (
    "Note: The data may involve significant wash-trading or bot activity. "
    "Figures have been preprocessed, including outlier removal, calibration, "
    "and standardization."
)
FOOTNOTE_TRAFFIC_CN = (
    "注：网站浏览量数据来自 Similarweb，可能受机器人流量与统计口径影响。"
    "上月流量为当期已发布数；本月为关账口径。"
)
FOOTNOTE_TRAFFIC_EN = (
    "Note: Website traffic data are from Similarweb and may be affected by "
    "bot traffic and measurement methodology. "
    "Prior-month traffic is as previously published; the current month uses the closing vintage."
)

MONTH_CN = {
    1: "1 月", 2: "2 月", 3: "3 月", 4: "4 月", 5: "5 月", 6: "6 月",
    7: "7 月", 8: "8 月", 9: "9 月", 10: "10 月", 11: "11 月", 12: "12 月",
}
MONTH_EN = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
}


def latest_report() -> Path:
    files = sorted(ROOT.glob("monthly_report_*.xlsx"))
    if not files:
        raise FileNotFoundError("no monthly_report_*.xlsx found")
    return files[-1]


def excel_serial_to_timestamp(value: float | int) -> pd.Timestamp:
    return pd.Timestamp("1899-12-30") + pd.Timedelta(days=float(value))


def parse_month(col) -> pd.Timestamp:
    if isinstance(col, pd.Timestamp):
        return col.normalize()
    if isinstance(col, (int, float)) and not isinstance(col, bool):
        if 20000 < float(col) < 80000:
            return excel_serial_to_timestamp(col).normalize()
    return pd.Timestamp(col).normalize()


def month_columns(df: pd.DataFrame) -> tuple[object, object, pd.Timestamp, pd.Timestamp]:
    months: list[tuple[object, pd.Timestamp]] = []
    for col in df.columns:
        if col in ("Exchange", "MoM %", "Geographic Location") or str(col).startswith("Unnamed"):
            continue
        try:
            months.append((col, parse_month(col)))
        except (ValueError, TypeError, OverflowError):
            continue
    if len(months) < 2:
        raise ValueError(f"need two month columns; got {[c for c, _ in months]}")
    months.sort(key=lambda x: x[1])
    prev_col, prev_ts = months[-2]
    curr_col, curr_ts = months[-1]
    return prev_col, curr_col, prev_ts, curr_ts


def load_report(
    path: Path,
) -> tuple[pd.Timestamp, pd.Timestamp, dict[str, pd.DataFrame], object, object]:
    sheets = pd.read_excel(path, sheet_name=None)
    sample = next(iter(sheets.values()))
    prev_col, curr_col, prev_m, curr_m = month_columns(sample)
    return prev_m, curr_m, sheets, prev_col, curr_col


def fmt_pct(v: float, *, signed: bool = False, digits: int = 1) -> str:
    pct = v * 100
    if signed:
        return f"{pct:+.{digits}f}%"
    return f"{abs(pct):.{digits}f}%"


def fmt_share(v: float) -> str:
    return f"{v * 100:.1f}%"


def fmt_ratio(v: float) -> str:
    return f"{v:.2f}x"


def fmt_usd_cn(v: float) -> str:
    yi = v / 1e8
    if abs(yi) >= 10_000:
        return f"{yi / 10_000:.2f} 万亿美元"
    if abs(yi) >= 100:
        return f"{yi:,.0f} 亿美元"
    if abs(yi) >= 1:
        return f"{yi:.1f} 亿美元"
    return f"{v / 1e4:,.0f} 万美元"


def fmt_usd_en(v: float) -> str:
    if abs(v) >= 1e12:
        return f"${v / 1e12:.2f} trillion"
    if abs(v) >= 1e9:
        return f"${v / 1e9:.1f} billion"
    if abs(v) >= 1e6:
        return f"${v / 1e6:.1f} million"
    return f"${v:,.0f}"


def dir_cn(m: float) -> str:
    return "上升" if m >= 0 else "下降"


def dir_en(m: float) -> str:
    return "increased" if m >= 0 else "decreased"


def sheet_sum(df: pd.DataFrame, month_col: object) -> float:
    row = df.loc[df["Exchange"] == "SUM"]
    if row.empty:
        raise KeyError("SUM row missing")
    return float(row[month_col].iloc[0])


def exchange_frame(df: pd.DataFrame, curr_col: object, prev_col: object) -> pd.DataFrame:
    sub = df[df["Exchange"] != "SUM"].copy()
    sub["curr"] = pd.to_numeric(sub[curr_col], errors="coerce")
    sub["prev"] = pd.to_numeric(sub[prev_col], errors="coerce")
    sub["mom"] = pd.to_numeric(sub["MoM %"], errors="coerce")
    total = float(sub["curr"].fillna(0).sum())
    sub["share"] = sub["curr"] / total if total else 0.0
    return sub.dropna(subset=["curr"])


def futures_spot_ratio(spot: pd.DataFrame, futures: pd.DataFrame, month_col: object) -> float:
    spot_sum = sheet_sum(spot, month_col)
    fut_sum = sheet_sum(futures, month_col)
    if spot_sum == 0 or pd.isna(spot_sum) or pd.isna(fut_sum):
        raise ValueError(f"cannot compute futures/spot ratio for column {month_col}")
    return fut_sum / spot_sum


def rank_vol_cn(rows: pd.DataFrame, n: int = 3) -> str:
    top = rows.nlargest(n, "curr")
    return "、".join(
        f"{r.Exchange} {fmt_usd_cn(float(r.curr))}（占比 {fmt_share(float(r.share))}）"
        for r in top.itertuples()
    )


def rank_vol_en(rows: pd.DataFrame, n: int = 3) -> str:
    top = rows.nlargest(n, "curr")
    return ", ".join(
        f"{r.Exchange} {fmt_usd_en(float(r.curr))} ({fmt_share(float(r.share))} share)"
        for r in top.itertuples()
    )


def rank_mom_cn(rows: pd.DataFrame, *, ascending: bool, n: int = 3) -> str:
    ranked = rows.nsmallest(n, "mom") if ascending else rows.nlargest(n, "mom")
    return "、".join(
        f"{r.Exchange} {fmt_usd_cn(float(r.curr))}（{fmt_pct(float(r.mom), signed=True)}）"
        for r in ranked.itertuples()
    )


def rank_mom_en(rows: pd.DataFrame, *, ascending: bool, n: int = 3) -> str:
    ranked = rows.nsmallest(n, "mom") if ascending else rows.nlargest(n, "mom")
    return ", ".join(
        f"{r.Exchange} {fmt_usd_en(float(r.curr))} ({fmt_pct(float(r.mom), signed=True)})"
        for r in ranked.itertuples()
    )


def concentration(rows: pd.DataFrame) -> tuple[float, float]:
    ordered = rows.sort_values("curr", ascending=False)
    shares = ordered["share"].tolist()
    top1 = float(shares[0]) if shares else 0.0
    top3 = float(sum(shares[:3])) if shares else 0.0
    return top1, top3


def breadth(rows: pd.DataFrame) -> tuple[int, int, int]:
    moms = rows["mom"].dropna()
    return int((moms > 0).sum()), int((moms < 0).sum()), int((moms == 0).sum())


def sample_breadth_cn(n: int, up: int, down: int, flat: int) -> str:
    bits = [f"环比上涨 {up} 家", f"下跌 {down} 家"]
    if flat:
        bits.append(f"持平 {flat} 家")
    return f"样本覆盖 {n} 家交易所，其中{'、'.join(bits)}。"


def sample_breadth_en(n: int, up: int, down: int, flat: int) -> str:
    extra = ""
    if flat:
        verb = "was" if flat == 1 else "were"
        extra = f" and {flat} {verb} unchanged"
    return (
        f"The sample covers {n} exchanges, of which {up} rose and {down} fell "
        f"month over month{extra}."
    )


def build_spot_narrative(
    prev_m: pd.Timestamp,
    curr_m: pd.Timestamp,
    sheets: dict[str, pd.DataFrame],
    prev_col: object,
    curr_col: object,
) -> tuple[str, str]:
    cy, cm = int(curr_m.year), int(curr_m.month)
    py, pm = int(prev_m.year), int(prev_m.month)
    spot = sheets["spot"]
    rows = exchange_frame(spot, curr_col, prev_col)
    curr = sheet_sum(spot, curr_col)
    prev = sheet_sum(spot, prev_col)
    mom = float(spot.loc[spot["Exchange"] == "SUM", "MoM %"].iloc[0])
    top1, top3 = concentration(rows)
    up, down, flat = breadth(rows)
    n = len(rows)

    title_cn = (
        f"{cy} 年 {MONTH_CN[cm]}交易所现货成交量报告："
        f"合计 {fmt_usd_cn(curr)}，环比{dir_cn(mom)} {fmt_pct(mom)}"
    )
    title_en = (
        f"{MONTH_EN[cm]} {cy} Exchange Spot Volume Report: "
        f"total {fmt_usd_en(curr)}, MoM {dir_en(mom)} {fmt_pct(mom)}"
    )
    cn = [
        title_cn,
        f"吴说数据中心统计显示：{DASHBOARD_VOLUME_URL}",
        "",
        f"{cy} 年 {MONTH_CN[cm]}，主要交易所现货成交量合计约 {fmt_usd_cn(curr)}，"
        f"相较 {py} 年 {MONTH_CN[pm]}的 {fmt_usd_cn(prev)} {dir_cn(mom)}约 {fmt_pct(mom)}。"
        f"{sample_breadth_cn(n, up, down, flat)}",
        "",
        f"按成交额排名，前三为 {rank_vol_cn(rows)}。"
        f"头部集中度方面，第一名占比约 {fmt_share(top1)}，前三合计占比约 {fmt_share(top3)}。",
        "",
        f"环比表现最好的三家为 {rank_mom_cn(rows, ascending=False)}；"
        f"跌幅最大的三家为 {rank_mom_cn(rows, ascending=True)}。",
        "",
        FOOTNOTE_VOLUME_CN,
    ]
    en = [
        title_en,
        f"Statistics compiled by the WuBlockchain team: {DASHBOARD_VOLUME_URL}",
        "",
        f"In {MONTH_EN[cm]} {cy}, spot trading volume across major exchanges totaled about "
        f"{fmt_usd_en(curr)}, {dir_en(mom)} approximately {fmt_pct(mom)} "
        f"from {fmt_usd_en(prev)} in {MONTH_EN[pm]} {py}. "
        f"{sample_breadth_en(n, up, down, flat)}",
        "",
        f"By notional volume, the top three were {rank_vol_en(rows)}. "
        f"On concentration, the largest venue held about {fmt_share(top1)}, "
        f"while the top three combined for about {fmt_share(top3)}.",
        "",
        f"The three strongest MoM performers were {rank_mom_en(rows, ascending=False)}; "
        f"the three largest declines were {rank_mom_en(rows, ascending=True)}.",
        "",
        FOOTNOTE_VOLUME_EN,
    ]
    return "\n".join(cn), "\n".join(en)


def build_futures_narrative(
    prev_m: pd.Timestamp,
    curr_m: pd.Timestamp,
    sheets: dict[str, pd.DataFrame],
    prev_col: object,
    curr_col: object,
) -> tuple[str, str]:
    cy, cm = int(curr_m.year), int(curr_m.month)
    py, pm = int(prev_m.year), int(prev_m.month)
    futures = sheets["futures"]
    spot = sheets["spot"]
    rows = exchange_frame(futures, curr_col, prev_col)
    curr = sheet_sum(futures, curr_col)
    prev = sheet_sum(futures, prev_col)
    mom = float(futures.loc[futures["Exchange"] == "SUM", "MoM %"].iloc[0])
    top1, top3 = concentration(rows)
    up, down, flat = breadth(rows)
    n = len(rows)
    ratio_curr = futures_spot_ratio(spot, futures, curr_col)
    ratio_prev = futures_spot_ratio(spot, futures, prev_col)
    ratio_mom = (ratio_curr - ratio_prev) / ratio_prev if ratio_prev else None

    title_cn = (
        f"{cy} 年 {MONTH_CN[cm]}交易所衍生品成交量报告："
        f"合计 {fmt_usd_cn(curr)}，环比{dir_cn(mom)} {fmt_pct(mom)}，"
        f"合约/现货比值 {fmt_ratio(ratio_curr)}"
    )
    title_en = (
        f"{MONTH_EN[cm]} {cy} Exchange Derivatives Volume Report: "
        f"total {fmt_usd_en(curr)}, MoM {dir_en(mom)} {fmt_pct(mom)}, "
        f"futures/spot ratio {fmt_ratio(ratio_curr)}"
    )
    if ratio_mom is None:
        ratio_cn = f"同期主要交易所合约/现货成交量比值为 {fmt_ratio(ratio_curr)}。"
        ratio_en = (
            f"The futures-to-spot volume ratio across major exchanges was {fmt_ratio(ratio_curr)}."
        )
    else:
        ratio_cn = (
            f"合约/现货成交量比值方面，{cy} 年 {MONTH_CN[cm]}为 {fmt_ratio(ratio_curr)}，"
            f"相较 {py} 年 {MONTH_CN[pm]}的 {fmt_ratio(ratio_prev)} "
            f"{dir_cn(ratio_mom)}约 {fmt_pct(ratio_mom)}。"
        )
        ratio_en = (
            f"The futures-to-spot volume ratio was {fmt_ratio(ratio_curr)} in {MONTH_EN[cm]} {cy}, "
            f"{dir_en(ratio_mom)} approximately {fmt_pct(ratio_mom)} "
            f"from {fmt_ratio(ratio_prev)} in {MONTH_EN[pm]} {py}."
        )

    cn = [
        title_cn,
        f"吴说数据中心统计显示：{DASHBOARD_VOLUME_URL}",
        "",
        f"{cy} 年 {MONTH_CN[cm]}，主要交易所衍生品成交量合计约 {fmt_usd_cn(curr)}，"
        f"相较 {py} 年 {MONTH_CN[pm]}的 {fmt_usd_cn(prev)} {dir_cn(mom)}约 {fmt_pct(mom)}。"
        f"{sample_breadth_cn(n, up, down, flat)}",
        "",
        f"按成交额排名，前三为 {rank_vol_cn(rows)}。"
        f"头部集中度方面，第一名占比约 {fmt_share(top1)}，前三合计占比约 {fmt_share(top3)}。",
        "",
        f"环比表现最好的三家为 {rank_mom_cn(rows, ascending=False)}；"
        f"跌幅最大的三家为 {rank_mom_cn(rows, ascending=True)}。",
        "",
        ratio_cn,
        "",
        FOOTNOTE_VOLUME_CN,
    ]
    en = [
        title_en,
        f"Statistics compiled by the WuBlockchain team: {DASHBOARD_VOLUME_URL}",
        "",
        f"In {MONTH_EN[cm]} {cy}, derivatives trading volume across major exchanges "
        f"totaled about {fmt_usd_en(curr)}, {dir_en(mom)} approximately {fmt_pct(mom)} "
        f"from {fmt_usd_en(prev)} in {MONTH_EN[pm]} {py}. "
        f"{sample_breadth_en(n, up, down, flat)}",
        "",
        f"By notional volume, the top three were {rank_vol_en(rows)}. "
        f"On concentration, the largest venue held about {fmt_share(top1)}, "
        f"while the top three combined for about {fmt_share(top3)}.",
        "",
        f"The three strongest MoM performers were {rank_mom_en(rows, ascending=False)}; "
        f"the three largest declines were {rank_mom_en(rows, ascending=True)}.",
        "",
        ratio_en,
        "",
        FOOTNOTE_VOLUME_EN,
    ]
    return "\n".join(cn), "\n".join(en)


def build_traffic_narrative(
    prev_m: pd.Timestamp,
    curr_m: pd.Timestamp,
    sheets: dict[str, pd.DataFrame],
    prev_col: object,
    curr_col: object,
) -> tuple[str, str]:
    traffic = sheets["traffic"]
    try:
        prev_col, curr_col, prev_m, curr_m = month_columns(traffic)
    except ValueError:
        pass
    cy, cm = int(curr_m.year), int(curr_m.month)
    py, pm = int(prev_m.year), int(prev_m.month)
    rows = exchange_frame(traffic, curr_col, prev_col)
    if rows.empty:
        raise ValueError("traffic sheet has no exchange rows")

    def fmt_visits_cn(v: float) -> str:
        # v is million visits; 1 亿 = 100M, 1 万 = 0.01M
        if abs(v) >= 100:
            return f"{v / 100:.2f} 亿次"
        return f"{v * 100:,.0f} 万次"

    def fmt_visits_en(v: float) -> str:
        return f"{v:.2f} million visits"

    curr = sheet_sum(traffic, curr_col)
    prev = sheet_sum(traffic, prev_col)
    mom = float(traffic.loc[traffic["Exchange"] == "SUM", "MoM %"].iloc[0])
    top1, top3 = concentration(rows)
    up, down, flat = breadth(rows)
    n = len(rows)

    def rank_vol_visits_cn(frame: pd.DataFrame, k: int = 3) -> str:
        top = frame.nlargest(k, "curr")
        return "、".join(
            f"{r.Exchange} {fmt_visits_cn(float(r.curr))}（占比 {fmt_share(float(r.share))}）"
            for r in top.itertuples()
        )

    def rank_vol_visits_en(frame: pd.DataFrame, k: int = 3) -> str:
        top = frame.nlargest(k, "curr")
        return ", ".join(
            f"{r.Exchange} {fmt_visits_en(float(r.curr))} ({fmt_share(float(r.share))} share)"
            for r in top.itertuples()
        )

    def rank_mom_visits_cn(frame: pd.DataFrame, *, ascending: bool, k: int = 3) -> str:
        ranked = frame.nsmallest(k, "mom") if ascending else frame.nlargest(k, "mom")
        return "、".join(
            f"{r.Exchange} {fmt_visits_cn(float(r.curr))}（{fmt_pct(float(r.mom), signed=True)}）"
            for r in ranked.itertuples()
        )

    def rank_mom_visits_en(frame: pd.DataFrame, *, ascending: bool, k: int = 3) -> str:
        ranked = frame.nsmallest(k, "mom") if ascending else frame.nlargest(k, "mom")
        return ", ".join(
            f"{r.Exchange} {fmt_visits_en(float(r.curr))} ({fmt_pct(float(r.mom), signed=True)})"
            for r in ranked.itertuples()
        )

    geo_bits_cn: list[str] = []
    geo_bits_en: list[str] = []
    if "Geographic Location" in traffic.columns:
        for _, row in rows.nlargest(3, "curr").iterrows():
            geo = row.get("Geographic Location")
            if isinstance(geo, str) and geo.strip():
                name = str(row["Exchange"])
                geo_bits_cn.append(f"{name} 主要访客来源为 {geo.strip()}")
                geo_bits_en.append(f"{name}'s top visitor origin was {geo.strip()}")

    title_cn = (
        f"{cy} 年 {MONTH_CN[cm]}交易所网站流量报告："
        f"合计约 {fmt_visits_cn(curr)}，环比{dir_cn(mom)} {fmt_pct(mom, digits=2)}"
    )
    title_en = (
        f"{MONTH_EN[cm]} {cy} Exchange Website Traffic Report: "
        f"total about {fmt_visits_en(curr)}, MoM {dir_en(mom)} {fmt_pct(mom, digits=2)}"
    )
    cn = [
        title_cn,
        "",
        f"吴说数据中心统计显示：{DASHBOARD_TRAFFIC_URL}",
        "",
        f"{cy} 年 {MONTH_CN[cm]}，主要交易所网站浏览量合计约 {fmt_visits_cn(curr)}，"
        f"相较 {py} 年 {MONTH_CN[pm]}的 {fmt_visits_cn(prev)} {dir_cn(mom)}约 {fmt_pct(mom, digits=2)}。"
        f"{sample_breadth_cn(n, up, down, flat)}",
        "",
        f"按浏览量排名，前三为 {rank_vol_visits_cn(rows)}。"
        f"头部集中度方面，第一名占比约 {fmt_share(top1)}，前三合计占比约 {fmt_share(top3)}。",
        "",
        f"环比表现最好的三家为 {rank_mom_visits_cn(rows, ascending=False)}；"
        f"跌幅最大的三家为 {rank_mom_visits_cn(rows, ascending=True)}。",
    ]
    if geo_bits_cn:
        cn += ["", "地域分布方面，" + "；".join(geo_bits_cn) + "。"]

    en = [
        title_en,
        "",
        f"Statistics compiled by the WuBlockchain team: {DASHBOARD_TRAFFIC_URL}",
        "",
        f"In {MONTH_EN[cm]} {cy}, website traffic across major exchanges totaled about "
        f"{fmt_visits_en(curr)}, {dir_en(mom)} approximately {fmt_pct(mom, digits=2)} "
        f"from {fmt_visits_en(prev)} in {MONTH_EN[pm]} {py}. "
        f"{sample_breadth_en(n, up, down, flat)}",
        "",
        f"By visits, the top three were {rank_vol_visits_en(rows)}. "
        f"Concentration: the largest venue held about {fmt_share(top1)}, "
        f"while the top three combined for about {fmt_share(top3)}.",
        "",
        f"The three strongest MoM performers were {rank_mom_visits_en(rows, ascending=False)}; "
        f"the three largest declines were {rank_mom_visits_en(rows, ascending=True)}.",
    ]
    if geo_bits_en:
        en += ["", "On geography, " + "; ".join(geo_bits_en) + "."]
    return "\n".join(cn), "\n".join(en)


def write_pair(out_dir: Path, stem: str, cn: str, en: str) -> tuple[Path, Path]:
    cn_path = out_dir / f"{stem}_zh.txt"
    en_path = out_dir / f"{stem}_en.txt"
    cn_path.write_text(cn, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    return cn_path, en_path


def traffic_usable(sheets: dict[str, pd.DataFrame]) -> bool:
    if "traffic" not in sheets:
        return False
    df = sheets["traffic"]
    if df.empty or "Exchange" not in df.columns:
        return False
    return bool((df["Exchange"] != "SUM").any())


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("report", nargs="?", type=Path, default=None, help="monthly_report xlsx")
    p.add_argument("--out-dir", type=Path, default=ROOT)
    p.add_argument(
        "--only",
        default="spot,futures,traffic",
        help="comma list: spot,futures,traffic (default: all available)",
    )
    args = p.parse_args()

    report = args.report or latest_report()
    if not report.is_file():
        raise FileNotFoundError(report)

    wanted = {x.strip().lower() for x in args.only.split(",") if x.strip()}
    unknown = wanted - {"spot", "futures", "traffic"}
    if unknown:
        raise SystemExit(f"unknown --only values: {', '.join(sorted(unknown))}")

    prev_m, curr_m, sheets, prev_col, curr_col = load_report(report)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    month_tag = f"{curr_m:%Y-%m}"
    written: list[str] = []

    builders = []
    if "spot" in wanted:
        builders.append(("spot", "monthly_narrative_spot", build_spot_narrative))
    if "futures" in wanted:
        builders.append(("futures", "monthly_narrative_futures", build_futures_narrative))
    if "traffic" in wanted:
        builders.append(("traffic", "monthly_narrative_traffic", build_traffic_narrative))

    for kind, stem_prefix, builder in builders:
        if kind == "traffic" and not traffic_usable(sheets):
            print("[skip] traffic sheet missing or empty")
            continue
        if kind in ("spot", "futures") and kind not in sheets:
            raise KeyError(f"{kind} sheet missing in {report.name}")
        cn, en = builder(prev_m, curr_m, sheets, prev_col, curr_col)
        paths = write_pair(args.out_dir, f"{stem_prefix}_{month_tag}", cn, en)
        print(cn)
        print("\n" + "=" * 60 + "\n")
        print(en)
        print("\n" + "=" * 60 + "\n")
        written.append(f"{paths[0].name} / {paths[1].name}")

    if not written:
        raise SystemExit("no narratives written")
    print("written " + "; ".join(written))


if __name__ == "__main__":
    main()
