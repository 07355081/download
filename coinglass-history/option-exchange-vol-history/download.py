"""GET /api/option/exchange-vol-history — BTC/ETH USD (no range param)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _runner import run_option_exchange_chart  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(
        run_option_exchange_chart(
            "/api/option/exchange-vol-history", __file__, with_range=False
        )
    )
