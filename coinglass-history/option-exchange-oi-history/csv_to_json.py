from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _runner import csv_to_json_wide_chart  # noqa: E402

if __name__ == "__main__":
    csv_to_json_wide_chart(__file__, interval_key="range", stem_parts=3)
