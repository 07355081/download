"""GET /api/etf/{market}/list — ETF ticker metadata → cache/{market}.json.

Use csv_to_json.py to publish output/json/{market}.json for dashboard.
Default markets: bitcoin, ethereum only (solana/xrp list 端点 v4 返回 404).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _runner import run_etf_list  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_etf_list(__file__))
