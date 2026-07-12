"""GET /api/spot/price/history — per (exchange, instrument_id, interval)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _runner import run_per_instrument  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_per_instrument("/api/spot/price/history", "spot", __file__))
