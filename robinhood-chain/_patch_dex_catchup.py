"""One-shot: fill Robinhood DEX gap from 2026-09-23, then restore the 2-day SQL."""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path("/root/data-download")
sys.path.insert(0, str(ROOT))
from _dune import API_BASE, resolve_api_key  # noqa: E402

CATCHUP_SQL = """
SELECT
    CAST(block_date AS VARCHAR) AS day,
    SUM(amount_usd) AS dex_volume_usd
FROM dex.trades
WHERE blockchain = 'robinhood'
  AND block_date >= DATE '2026-09-23'
  AND block_date < CURRENT_DATE
GROUP BY 1
ORDER BY 1
"""

QUERY_ID = 8894975


def main() -> None:
    api_key = resolve_api_key(None, ROOT)
    headers = {"X-Dune-API-Key": api_key, "Content-Type": "application/json"}
    body = json.dumps({"query_sql": CATCHUP_SQL}).encode("utf-8")
    request = urllib.request.Request(
        f"{API_BASE}/query/{QUERY_ID}",
        data=body,
        headers=headers,
        method="PATCH",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        response.read()
    print(f"patched query {QUERY_ID} to catch up from 2026-09-23")


if __name__ == "__main__":
    main()
