"""GET /api/option/max-pain — append snapshots per (symbol, exchange) → cache/*.json.

Use csv_to_json.py to publish output/json/*.json for dashboard.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _runner import run_option_max_pain  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_option_max_pain(__file__))
