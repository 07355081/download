"""Monthly stablecoin-card volume by chain from the public Paymentscan page.

The chains page embeds one row per month and chain. This keeps that table and
drops the rest of the page. It does not call the paid API and it does not use Dune.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "stablecoin_card_volume.json"
PAGE_URL = "https://paymentscan.xyz/chains"
ROW_RE = re.compile(r'\{date:"(\d{4}-\d{2}-\d{2})",chain:"([^"]+)",volumes:([0-9.]+)')
MIN_ROWS = 400

NAMES = {
    "tron": "TRON",
    "base": "Base",
    "bsc": "BNB Chain",
    "ethereum": "Ethereum",
    "optimism": "Optimism",
    "solana": "Solana",
    "plasma": "Plasma",
    "polygon": "Polygon",
    "arbitrum": "Arbitrum",
    "stellar": "Stellar",
    "arc": "Arc",
    "bitcoin": "Bitcoin",
    "gnosis": "Gnosis",
    "avalanche": "Avalanche",
    "linea": "Linea",
    "scroll": "Scroll",
    "zksync": "zkSync",
    "starknet": "Starknet",
    "ton": "TON",
    "offchain": "链下",
    "hyperevm": "HyperEVM",
    "sonic": "Sonic",
}


def display_name(chain_id: str) -> str:
    if chain_id in NAMES:
        return NAMES[chain_id]
    return chain_id.replace("-", " ").title()


def fetch_page() -> str:
    req = urllib.request.Request(PAGE_URL, headers={"User-Agent": "Mozilla/5.0 dashboard-data/stablecoin-cards"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        if resp.status != 200:
            raise RuntimeError(f"page status {resp.status}")
        return resp.read().decode("utf-8", "replace")


def build(text: str) -> dict:
    found = ROW_RE.findall(text)
    if len(found) < MIN_ROWS:
        raise RuntimeError(f"only {len(found)} chain rows, refuse to publish")
    buckets: dict[str, dict[str, float]] = defaultdict(dict)
    for day, chain_id, volume in found:
        month = day[:7]
        buckets[month][chain_id] = buckets[month].get(chain_id, 0.0) + float(volume)
    months = []
    for month in sorted(buckets):
        chains = [
            {"id": chain_id, "name": display_name(chain_id), "volume_usd": round(volume, 2)}
            for chain_id, volume in sorted(buckets[month].items(), key=lambda item: -item[1])
            if volume > 0
        ]
        months.append({
            "month": month,
            "total_usd": round(sum(row["volume_usd"] for row in chains), 2),
            "chains": chains,
        })
    if len(months) < 12:
        raise RuntimeError(f"only {len(months)} months, refuse to publish")
    return {
        "code": "0",
        "msg": "success",
        "name": "stablecoin_card_volume",
        "description": "Paymentscan 公开链页面的稳定币卡交易量，按月按链。不是付费 API。",
        "source": "paymentscan-public",
        "source_url": PAGE_URL,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "month_count": len(months),
        "months": months,
    }


def main() -> int:
    print("GET", PAGE_URL)
    payload = build(fetch_page())
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    latest = payload["months"][-1]
    print(f"wrote {OUT_PATH} months={payload['month_count']} latest={latest['month']} {latest['total_usd'] / 1e8:.2f}亿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
