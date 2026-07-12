"""Generate CN/EN monthly narrative from monthly_report_YYYY-MM.xlsx."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
FOOTNOTE_CN = (
    "注：以下数据可能存在严重的刷量/机器人嫌疑，相关数据已经过预处理，"
    "包括异常值剔除、口径校准与标准化处理。现货与衍生品原始数据来自 Coingecko；"
    "流量数据来自 Similarweb。"
)
FOOTNOTE_EN = (
    "Note: The data below may involve significant wash-trading or bot activity. "
    "All figures have been preprocessed, including outlier removal, calibration, "
    "and standardization. Spot and derivatives raw data are from CoinGecko; "
    "traffic data are from Similarweb."
)

MONTH_CN = {
    1: "1月", 2: "2月", 3: "3月", 4: "4月", 5: "5月", 6: "6月",
    7: "7月", 8: "8月", 9: "9月", 10: "10月", 11: "11月", 12: "12月",
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


def parse_month(col) -> pd.Timestamp:
    return pd.Timestamp(col)


def fmt_pct(v: float, *, signed: bool = False) -> str:
    pct = v * 100
    if signed:
        return f"{pct:+.1f}%"
    return f"{abs(pct):.1f}%"


def fmt_pct_plain(v: float) -> str:
    return f"{v * 100:.2f}%"


def rank_line_cn(items: list[tuple[str, float]]) -> str:
    return "、".join(f"{name} {fmt_pct(m, signed=True)}" for name, m in items)


def rank_line_en(items: list[tuple[str, float]]) -> str:
    return ", ".join(f"{name} {fmt_pct(m, signed=True)}" for name, m in items)


def top_bottom(df: pd.DataFrame) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    sub = df[df["Exchange"] != "SUM"].copy()
    top = [(r["Exchange"], float(r["MoM %"])) for _, r in sub.nlargest(3, "MoM %").iterrows()]
    bottom = [(r["Exchange"], float(r["MoM %"])) for _, r in sub.nsmallest(3, "MoM %").iterrows()]
    return top, bottom


def load_report(path: Path) -> tuple[pd.Timestamp, pd.Timestamp, dict[str, pd.DataFrame]]:
    sheets = pd.read_excel(path, sheet_name=None)
    sample = next(iter(sheets.values()))
    prev_m = parse_month(sample.columns[1])
    curr_m = parse_month(sample.columns[2])
    return prev_m, curr_m, sheets


def build_narrative(prev_m: pd.Timestamp, curr_m: pd.Timestamp, sheets: dict[str, pd.DataFrame]) -> tuple[str, str]:
    cy, cm = int(curr_m.year), int(curr_m.month)
    py, pm = int(prev_m.year), int(prev_m.month)

    spot = sheets["spot"]
    futures = sheets["futures"]
    traffic = sheets["traffic"]

    spot_mom = float(spot.loc[spot["Exchange"] == "SUM", "MoM %"].iloc[0])
    fut_mom = float(futures.loc[futures["Exchange"] == "SUM", "MoM %"].iloc[0])
    traf_mom = float(traffic.loc[traffic["Exchange"] == "SUM", "MoM %"].iloc[0])

    spot_top, spot_bot = top_bottom(spot)
    fut_top, fut_bot = top_bottom(futures)
    traf_top, traf_bot = top_bottom(traffic)

    def dir_cn(m: float) -> str:
        return "上升" if m >= 0 else "下降"

    def dir_en(m: float) -> str:
        return "increased" if m >= 0 else "decreased"

    title_cn = (
        f"{cy} 年 {MONTH_CN[cm]}交易所数据报告："
        f"现货交易量{dir_cn(spot_mom)} {fmt_pct(spot_mom)}，"
        f"衍生品交易量{dir_cn(fut_mom)} {fmt_pct(fut_mom)}，"
        f"网站浏览量{dir_cn(traf_mom)} {fmt_pct_plain(abs(traf_mom))}"
    )
    title_en = (
        f"{MONTH_EN[cm]} {cy} Exchange Data Report: "
        f"spot volume {dir_en(spot_mom)} {fmt_pct(spot_mom)}, "
        f"derivatives volume {dir_en(fut_mom)} {fmt_pct(fut_mom)}, "
        f"website traffic {dir_en(traf_mom)} {fmt_pct_plain(abs(traf_mom))}"
    )

    cn = [
        title_cn,
        "",
        "吴说团队进行的数据统计显示：",
        "",
        f"{cy} 年 {MONTH_CN[cm]}主要交易所的现货交易量相较 {py} 年 {MONTH_CN[pm]} {dir_cn(spot_mom)}约 {fmt_pct(spot_mom)}。"
        f"表现排名前三为 {rank_line_cn(spot_top)}。"
        f"降幅排名前三为 {rank_line_cn(spot_bot)}。",
        "",
        f"{cy} 年 {MONTH_CN[cm]}主要交易所的衍生品交易量相较 {py} 年 {MONTH_CN[pm]} {dir_cn(fut_mom)}约 {fmt_pct(fut_mom)}。"
        f"增长排名前三为 {rank_line_cn(fut_top)}。"
        f"降幅排名前三为 {rank_line_cn(fut_bot)}。",
        "",
        f"{cy} 年 {MONTH_CN[cm]}主要交易所的网站浏览量相较 {py} 年 {MONTH_CN[pm]} {dir_cn(traf_mom)}约 {fmt_pct_plain(abs(traf_mom))}。"
        f"表现排名前三名为 {rank_line_cn(traf_top)}。"
        f"跌幅排名前三名为 {rank_line_cn(traf_bot)}。",
        "",
        FOOTNOTE_CN,
    ]

    en = [
        title_en,
        "",
        "Statistics compiled by the Wu Blockchain team show:",
        "",
        f"In {MONTH_EN[cm]} {cy}, spot trading volume across major exchanges "
        f"{dir_en(spot_mom)} approximately {fmt_pct(spot_mom)} compared with {MONTH_EN[pm]} {py}. "
        f"The top three performers were {rank_line_en(spot_top)}. "
        f"The three largest declines were {rank_line_en(spot_bot)}.",
        "",
        f"In {MONTH_EN[cm]} {cy}, derivatives trading volume across major exchanges "
        f"{dir_en(fut_mom)} approximately {fmt_pct(fut_mom)} compared with {MONTH_EN[pm]} {py}. "
        f"The top three gainers were {rank_line_en(fut_top)}. "
        f"The three largest declines were {rank_line_en(fut_bot)}.",
        "",
        f"In {MONTH_EN[cm]} {cy}, website traffic across major exchanges "
        f"{dir_en(traf_mom)} approximately {fmt_pct_plain(abs(traf_mom))} compared with {MONTH_EN[pm]} {py}. "
        f"The top three performers were {rank_line_en(traf_top)}. "
        f"The three largest declines were {rank_line_en(traf_bot)}.",
        "",
        FOOTNOTE_EN,
    ]
    return "\n".join(cn), "\n".join(en)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("report", nargs="?", type=Path, default=None, help="monthly_report xlsx")
    p.add_argument("--out-dir", type=Path, default=ROOT)
    args = p.parse_args()

    report = args.report or latest_report()
    if not report.is_file():
        raise FileNotFoundError(report)

    prev_m, curr_m, sheets = load_report(report)
    cn, en = build_narrative(prev_m, curr_m, sheets)

    stem = f"monthly_narrative_{curr_m:%Y-%m}"
    cn_path = args.out_dir / f"{stem}_zh.txt"
    en_path = args.out_dir / f"{stem}_en.txt"
    cn_path.write_text(cn, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    print(cn)
    print("\n" + "=" * 60 + "\n")
    print(en)
    print(f"\nwritten {cn_path.name} / {en_path.name}")


if __name__ == "__main__":
    main()
