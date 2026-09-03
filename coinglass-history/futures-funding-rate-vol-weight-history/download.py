"""GET /api/futures/funding-rate/vol-weight-history — per coin."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _runner import run_per_symbol_main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(
        run_per_symbol_main(
            "/api/futures/funding-rate/vol-weight-history",
            __file__,
            symbol_scope="all",
        )
    )
