"""Convert etf-list cache JSON to dashboard output/json."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _runner import csv_to_json_etf_list  # noqa: E402

if __name__ == "__main__":
    csv_to_json_etf_list(__file__)
