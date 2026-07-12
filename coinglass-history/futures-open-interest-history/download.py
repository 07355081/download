"""GET /api/futures/open-interest/history — per exchange pair OI OHLC."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _runner import run_per_instrument  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(
        run_per_instrument(
            "/api/futures/open-interest/history",
            "futures",
            __file__,
            extra_params={"unit": "usd"},
        )
    )
