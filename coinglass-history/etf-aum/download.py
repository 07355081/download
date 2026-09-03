"""GET /api/etf/{market}/aum — aggregate AUM time series."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _runner import run_etf_aum  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_etf_aum(__file__))
