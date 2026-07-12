#!/usr/bin/env python3
"""Run regime composite strategy — fixed thresholds, train/OOS + walk-forward.

Usage:
    python run_backtest.py
    python run_backtest.py --train-ratio 0.7 --out output/backtest_summary.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from backtest import (
    BacktestResult,
    _annualize_return,
    _max_drawdown,
    _sharpe,
    buy_and_hold,
    run_backtest,
    summarize,
)
from data_loader import load_feature_panel
from signals import composite_score, score_to_position

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = SCRIPT_DIR / "output"


def _split_panel(panel: pd.DataFrame, train_ratio: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    panel = panel.dropna(subset=["close", "ret"])
    n = len(panel)
    cut = int(n * train_ratio)
    if cut < 252 or n - cut < 126:
        raise ValueError(f"Not enough rows for split (n={n}, train_ratio={train_ratio})")
    return panel.iloc[:cut], panel.iloc[cut:]


def walk_forward(
    panel: pd.DataFrame,
    train_years: int = 4,
    test_years: int = 1,
) -> list[BacktestResult]:
    """Rolling OOS: fit nothing, only evaluate fixed rules on each test window."""
    train_days = train_years * 365
    test_days = test_years * 365
    results: list[BacktestResult] = []
    i = train_days
    fold = 0
    while i + test_days <= len(panel):
        fold += 1
        test = panel.iloc[i : i + test_days]
        score = composite_score(test)
        pos = score_to_position(score)
        results.append(
            run_backtest(test, pos, label=f"wf_fold{fold}_oos")
        )
        i += test_days
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--train-ratio", type=float, default=0.7, help="In-sample fraction by time (default 0.7)")
    parser.add_argument("--out", default=str(DEFAULT_OUT / "backtest_summary.csv"))
    parser.add_argument("--equity-out", default=str(DEFAULT_OUT / "equity_curve.csv"))
    args = parser.parse_args()

    panel = load_feature_panel()
    needed = ["mvrv_z", "fear_greed", "ma_200w", "close", "ret"]
    full = panel.dropna(subset=needed).copy()
    print(f"Aligned panel: {full.index.min().date()} .. {full.index.max().date()}  ({len(full)} days)\n")

    score = composite_score(full)
    position = score_to_position(score)

    train, test = _split_panel(full, args.train_ratio)
    score_tr = composite_score(train)
    score_te = composite_score(test)

    results: list[BacktestResult] = [
        run_backtest(train, score_to_position(score_tr), label="strategy_in_sample"),
        run_backtest(test, score_to_position(score_te), label="strategy_oos"),
        buy_and_hold(train, label="buy_hold_in_sample"),
        buy_and_hold(test, label="buy_hold_oos"),
        run_backtest(full, position, label="strategy_full"),
        buy_and_hold(full, label="buy_hold_full"),
    ]

    wf = walk_forward(full)
    if wf:
        wf_equity = pd.concat([r.equity for r in wf], axis=0).sort_index()
        wf_equity = wf_equity[~wf_equity.index.duplicated(keep="last")]
        wf_ret = wf_equity.pct_change()
        results.append(
            BacktestResult(
                label="walk_forward_oos_combined",
                equity=wf_equity,
                position=pd.Series(dtype=float),
                stats={
                    "cagr": _annualize_return(wf_ret),
                    "sharpe": _sharpe(wf_ret),
                    "max_dd": _max_drawdown(wf_equity),
                    "vol_ann": float(wf_ret.std() * (365.25**0.5)),
                    "avg_position": float("nan"),
                    "days": float(wf_ret.notna().sum()),
                },
                trades_per_year=float("nan"),
            )
        )

    summary = summarize(results)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(out_path, float_format="%.4f")

    equity_df = pd.DataFrame({"close": full["close"], "score": score, "position": position})
    for r in results:
        if r.label == "strategy_full":
            equity_df["strategy_equity"] = r.equity
        if r.label == "buy_hold_full":
            equity_df["buy_hold_equity"] = r.equity
    eq_path = Path(args.equity_out)
    equity_df.to_csv(eq_path)

    pd.options.display.float_format = lambda x: f"{x:8.4f}"
    print("=== Performance (fixed thresholds, no parameter search) ===\n")
    print(summary.to_string())
    print(f"\nSaved summary -> {out_path}")
    print(f"Saved equity  -> {eq_path}")

    oos = summary.loc["strategy_oos"]
    bh = summary.loc["buy_hold_oos"]
    print("\n=== OOS vs buy-and-hold (last {:.0%} of sample) ===".format(1 - args.train_ratio))
    print(f"  Strategy CAGR {oos['cagr']:.2%}  |  B&H {bh['cagr']:.2%}")
    print(f"  Strategy Sharpe {oos['sharpe']:.2f}  |  B&H {bh['sharpe']:.2f}")
    print(f"  Strategy max DD {oos['max_dd']:.2%}  |  B&H {bh['max_dd']:.2%}")
    print("\nNote: thresholds are preset (MVRV Z 0/6, F&G 25/75, 200W MA).")
    print("      Do not tune them on OOS; walk-forward folds are additional sanity checks.")


if __name__ == "__main__":
    main()
