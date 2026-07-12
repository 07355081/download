"""Multi-factor ML stack: all indicators -> MLP with val early-stop (weights learned, not hand-tuned)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from backtest import BacktestResult, run_backtest
from load_all_features import load_all_features


@dataclass
class MLSplit:
    train: pd.DataFrame
    val: pd.DataFrame
    test: pd.DataFrame
    feature_cols: list[str]


def time_split(
    panel: pd.DataFrame,
    train_ratio: float = 0.6,
    val_ratio: float = 0.2,
) -> MLSplit:
    panel = panel.dropna(subset=["close", "ret"]).copy()
    n = len(panel)
    i1 = int(n * train_ratio)
    i2 = int(n * (train_ratio + val_ratio))
    feat = [c for c in panel.columns if c not in ("close", "ret")]
    return MLSplit(
        train=panel.iloc[:i1],
        val=panel.iloc[i1:i2],
        test=panel.iloc[i2:],
        feature_cols=feat,
    )


def rolling_zscore_features(panel: pd.DataFrame, cols: list[str], window: int = 252) -> pd.DataFrame:
    """Past-only rolling z-score (no lookahead within each day)."""
    out = panel[["close", "ret"]].copy()
    minp = max(30, window // 4)
    zcols = {}
    for c in cols:
        s = panel[c]
        mu = s.rolling(window, min_periods=minp).mean()
        sd = s.rolling(window, min_periods=minp).std().replace(0, np.nan)
        zcols[c] = (s - mu) / sd
    return pd.concat([out, pd.DataFrame(zcols, index=panel.index)], axis=1)


def build_xy(
    df: pd.DataFrame,
    feature_cols: list[str],
    horizon: int = 5,
) -> tuple[pd.DataFrame, pd.Series]:
    """Features at t predict cumulative forward return over next `horizon` days."""
    fwd = df["close"].shift(-horizon) / df["close"] - 1.0
    x = df[feature_cols]
    mask = fwd.notna()
    return x.loc[mask], fwd.loc[mask]


def prepare_features(panel: pd.DataFrame, feat_cols: list[str], ffill_limit: int = 5) -> pd.DataFrame:
    out = panel[["close", "ret"]].copy()
    block = panel[feat_cols].ffill(limit=ffill_limit)
    out = pd.concat([out, block], axis=1)
    return out


def train_mlp(
    x_train: pd.DataFrame,
    y_train: pd.Series,
    x_val: pd.DataFrame,
    y_val: pd.Series,
    *,
    seed: int = 42,
) -> Pipeline:
    model = MLPRegressor(
        hidden_layer_sizes=(64, 32),
        activation="relu",
        solver="adam",
        alpha=0.05,
        batch_size=128,
        learning_rate="adaptive",
        learning_rate_init=5e-4,
        max_iter=300,
        early_stopping=True,
        validation_fraction=0.15,
        n_iter_no_change=20,
        random_state=seed,
        verbose=False,
    )
    pipe = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("mlp", model),
        ]
    )
    pipe.fit(x_train.to_numpy(), y_train.to_numpy())
    # sklearn MLP uses internal val split; we also report external val below
    return pipe


def pred_to_position(pred: pd.Series, deadband: float = 0.0) -> pd.Series:
    """Map predicted forward return to [0, 1] BTC weight."""
    z = pred / (pred.abs().quantile(0.9) + 1e-8)
    z = z.clip(-2, 2)
    pos = 0.5 + 0.5 * np.tanh(z)
    if deadband > 0:
        pos = pos.where(pred.abs() > deadband, 0.3)
    return pos.clip(0.0, 1.0)


def fit_and_backtest(
    panel: pd.DataFrame | None = None,
    *,
    train_ratio: float = 0.6,
    val_ratio: float = 0.2,
    horizon: int = 5,
    z_window: int = 252,
    seed: int = 42,
) -> tuple[Pipeline, MLSplit, list[BacktestResult], pd.DataFrame]:
    raw = panel if panel is not None else load_all_features()
    feat_cols = [c for c in raw.columns if c not in ("close", "ret")]
    prep = prepare_features(raw, feat_cols)
    split = time_split(prep, train_ratio, val_ratio)

    # Drop sparse columns using train-set coverage only (no test leakage).
    cov = split.train[feat_cols].notna().mean()
    split.feature_cols = cov[(cov >= 0.5) & (cov > 0)].index.tolist()
    # Must have at least one non-NaN in train for imputer.
    var_ok = split.train[split.feature_cols].std(skipna=True) > 1e-12
    split.feature_cols = var_ok[var_ok].index.tolist()
    if len(split.feature_cols) < 10:
        raise ValueError(f"Too few features after train coverage filter: {len(split.feature_cols)}")

    z_train = rolling_zscore_features(split.train, split.feature_cols, z_window)
    z_val = rolling_zscore_features(split.val, split.feature_cols, z_window)
    z_test = rolling_zscore_features(split.test, split.feature_cols, z_window)
    z_full = rolling_zscore_features(prep, split.feature_cols, z_window)

    x_tr, y_tr = build_xy(z_train, split.feature_cols, horizon)
    x_va, y_va = build_xy(z_val, split.feature_cols, horizon)
    x_te, y_te = build_xy(z_test, split.feature_cols, horizon)

    if len(x_tr) < 200:
        raise ValueError(f"Too few training rows after NA drop: {len(x_tr)}")

    pipe = train_mlp(x_tr, y_tr, x_va, y_va, seed=seed)

    def _run_block(zdf: pd.DataFrame, label: str) -> BacktestResult:
        x, _ = build_xy(zdf, split.feature_cols, horizon)
        if x.empty:
            raise ValueError(f"No rows for {label}")
        pred = pd.Series(pipe.predict(x.to_numpy()), index=x.index, name="pred")
        pos = pred_to_position(pred)
        block = zdf.loc[x.index].copy()
        return run_backtest(block, pos, label=label)

    results = [
        _run_block(z_train, "ml_train"),
        _run_block(z_val, "ml_val"),
        _run_block(z_test, "ml_oos"),
    ]

    x_full, _ = build_xy(z_full, split.feature_cols, horizon)
    pred_full = pd.Series(pipe.predict(x_full.to_numpy()), index=x_full.index)
    pos_full = pred_to_position(pred_full)
    results.append(run_backtest(z_full.loc[x_full.index], pos_full, label="ml_full"))

    mlp = pipe.named_steps["mlp"]
    n_in = mlp.coefs_[0].shape[0] if mlp.coefs_ else 0
    w0 = np.abs(mlp.coefs_[0]).mean(axis=1) if n_in else np.array([])
    feat_names = split.feature_cols[:n_in]
    if len(feat_names) < n_in:
        feat_names = feat_names + [f"_dropped_{i}" for i in range(n_in - len(feat_names))]
    importance = pd.DataFrame(
        {"feature": feat_names, "weight_proxy": w0[: len(feat_names)]}
    ).sort_values("weight_proxy", ascending=False)

    return pipe, split, results, importance


def save_model(pipe: Pipeline, path: Path) -> None:
    import joblib

    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipe, path)
