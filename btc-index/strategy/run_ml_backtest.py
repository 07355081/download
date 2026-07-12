#!/usr/bin/env python3
"""Train MLP on ALL on-chain/sentiment factors; weights via gradient + early stopping.

Usage:
    python run_ml_backtest.py
    python run_ml_backtest.py --horizon 5 --train-ratio 0.6

Important:
    - "Global optimum" is only on TRAIN+VAL; TEST is untouched hold-out.
    - High feature count vs sample size => overfitting risk remains; compare ml_oos vs buy_hold.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from backtest import buy_and_hold, summarize
from ml_model import fit_and_backtest, save_model

SCRIPT_DIR = Path(__file__).resolve().parent
OUT = SCRIPT_DIR / "output"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--train-ratio", type=float, default=0.6)
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--horizon", type=int, default=5, help="Forward return days to predict")
    parser.add_argument("--min-coverage", type=float, default=0.35)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    print("Loading all indicator JSON files...")
    from load_all_features import load_all_features

    panel = load_all_features(min_coverage=args.min_coverage)

    print(f"\nTraining MLP ({panel.shape[1] - 2} features, horizon={args.horizon}d)...")
    pipe, split, results, imp = fit_and_backtest(
        panel,
        train_ratio=args.train_ratio,
        val_ratio=args.val_ratio,
        horizon=args.horizon,
        seed=args.seed,
    )

    # Buy & hold on same test window
    from ml_model import rolling_zscore_features

    z_test = rolling_zscore_features(split.test, split.feature_cols)
    results.append(buy_and_hold(z_test, label="buy_hold_test"))

    summary = summarize(results)
    OUT.mkdir(parents=True, exist_ok=True)
    summary_path = OUT / "ml_backtest_summary.csv"
    summary.to_csv(summary_path)

    imp_path = OUT / "ml_feature_scale_top30.csv"
    imp.head(30).to_csv(imp_path, index=False)

    model_path = OUT / "ml_mlp_model.joblib"
    save_model(pipe, model_path)

    pd.options.display.float_format = lambda x: f"{x:8.4f}"
    print("\n=== ML multi-factor (MLP, all indicators) ===\n")
    print(summary.to_string())
    print(f"\nTop scaled features (proxy) -> {imp_path}")
    print(f"Summary -> {summary_path}")
    print(f"Model   -> {model_path}")

    if "ml_oos" in summary.index and "buy_hold_test" in summary.index:
        ml = summary.loc["ml_oos"]
        bh = summary.loc["buy_hold_test"]
        print("\n=== Hold-out TEST (never used in weight fitting) ===")
        print(f"  ML CAGR {ml['cagr']:.2%}  Sharpe {ml['sharpe']:.2f}  maxDD {ml['max_dd']:.2%}")
        print(f"  B&H CAGR {bh['cagr']:.2%}  Sharpe {bh['sharpe']:.2f}  maxDD {bh['max_dd']:.2%}")
        print(
            f"\n  Features: {len(split.feature_cols)}  |  "
            f"Train/val/test rows: {len(split.train)}/{len(split.val)}/{len(split.test)}"
        )

    print(
        "\nNote: Adam finds a local optimum, not a guaranteed global one. "
        "High train CAGR with weak ml_oos usually means overfit — trust the hold-out split."
    )


if __name__ == "__main__":
    main()
