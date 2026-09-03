"""Simple daily backtest with train/OOS split and optional walk-forward."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_COST_BPS = 10.0  # per unit change in position


@dataclass
class BacktestResult:
    label: str
    equity: pd.Series
    position: pd.Series
    stats: dict[str, float]
    trades_per_year: float


def _max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min())


def _annualize_return(daily_ret: pd.Series) -> float:
    r = daily_ret.dropna()
    if r.empty:
        return float("nan")
    return float((1.0 + r).prod() ** (365.25 / len(r)) - 1.0)


def _sharpe(daily_ret: pd.Series) -> float:
    r = daily_ret.dropna()
    if len(r) < 2 or r.std() == 0:
        return float("nan")
    return float(r.mean() / r.std() * np.sqrt(365.25))


def run_backtest(
    panel: pd.DataFrame,
    position: pd.Series,
    label: str = "strategy",
    cost_bps: float = TRADING_COST_BPS,
) -> BacktestResult:
    """
    Signal at close t -> position for return t+1 (no lookahead).
    """
    pos = position.reindex(panel.index).ffill().fillna(0.0)
    pos_lag = pos.shift(1).fillna(0.0)
    turnover = pos_lag.diff().abs().fillna(pos_lag.abs())
    cost = turnover * (cost_bps / 10_000.0)

    strat_ret = pos_lag * panel["ret"] - cost
    equity = (1.0 + strat_ret.fillna(0.0)).cumprod()

    stats = {
        "cagr": _annualize_return(strat_ret),
        "sharpe": _sharpe(strat_ret),
        "max_dd": _max_drawdown(equity),
        "vol_ann": float(strat_ret.std() * np.sqrt(365.25)),
        "avg_position": float(pos_lag.mean()),
        "days": float(strat_ret.notna().sum()),
    }
    years = max(stats["days"] / 365.25, 1e-6)
    trades = float((turnover > 1e-6).sum())
    return BacktestResult(
        label=label,
        equity=equity,
        position=pos_lag,
        stats=stats,
        trades_per_year=trades / years,
    )


def buy_and_hold(panel: pd.DataFrame, label: str = "buy_hold") -> BacktestResult:
    pos = pd.Series(1.0, index=panel.index, name="position")
    return run_backtest(panel, pos, label=label, cost_bps=0.0)


def summarize(results: list[BacktestResult]) -> pd.DataFrame:
    rows = []
    for r in results:
        row = {"label": r.label, **r.stats, "trades_per_year": r.trades_per_year}
        rows.append(row)
    return pd.DataFrame(rows).set_index("label")
