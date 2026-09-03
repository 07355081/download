"""Fixed-threshold signals — thresholds are preset, not fitted on history."""
from __future__ import annotations

import pandas as pd

# Literature / index definition levels (do not optimize on backtest sample).
MVRV_Z_LOW = 0.0
MVRV_Z_HIGH = 6.0
FEAR_GREED_LOW = 25.0
FEAR_GREED_HIGH = 75.0


def composite_score(panel: pd.DataFrame) -> pd.Series:
    """
    Three orthogonal buckets, each in {-1, 0, +1}:
      - valuation: MVRV Z-Score
      - sentiment: Fear & Greed (contrarian)
      - trend: price vs 200-week moving average
    """
    z = panel["mvrv_z"]
    fg = panel["fear_greed"]
    trend = panel["above_200w"]

    val = pd.Series(0.0, index=panel.index)
    val = val.mask(z < MVRV_Z_LOW, 1.0)
    val = val.mask(z > MVRV_Z_HIGH, -1.0)

    sent = pd.Series(0.0, index=panel.index)
    sent = sent.mask(fg < FEAR_GREED_LOW, 1.0)
    sent = sent.mask(fg > FEAR_GREED_HIGH, -1.0)

    tr = pd.Series(0.0, index=panel.index)
    tr = tr.mask(trend > 0.5, 1.0)
    tr = tr.mask(trend < 0.5, -1.0)

    return (val + sent + tr).rename("score")


def score_to_position(score: pd.Series) -> pd.Series:
    """Map composite score (-3..+3) to target BTC weight with fixed breakpoints."""
    pos = pd.Series(0.3, index=score.index, name="position")
    pos = pos.mask(score >= 2, 1.0)
    pos = pos.mask(score == 1, 0.6)
    pos = pos.mask(score == 0, 0.3)
    pos = pos.mask(score == -1, 0.1)
    pos = pos.mask(score <= -2, 0.0)
    return pos
