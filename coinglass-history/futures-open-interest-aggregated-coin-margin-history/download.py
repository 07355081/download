"""GET /api/futures/open-interest/aggregated-coin-margin-history"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _runner import EXCHANGE_LIST_PARAM, run_per_symbol_main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(
        run_per_symbol_main(
            "/api/futures/open-interest/aggregated-coin-margin-history",
            __file__,
            extra_params={"exchange_list": EXCHANGE_LIST_PARAM, "unit": "usd"},
            symbol_scope="all",
        )
    )
