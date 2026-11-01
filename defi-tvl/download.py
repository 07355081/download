"""Download global + major-chain DeFi TVL from DefiLlama (free, no Dune).

Outputs:
  output/json/defi_tvl.json
    data: [{time, global, ethereum, solana, ...}, ...]  # USD, daily
    latest_chains: [{name, key, tvl}, ...]               # current snapshot ranking

Usage:
  python download.py
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
JSON_DIR = HERE / "output" / "json"
OUT_PATH = JSON_DIR / "defi_tvl.json"

API = "https://api.llama.fi"
UA = "dashboard-data/defi-tvl"
TIMEOUT = 90
ATTEMPTS = 3
SLEEP = 0.35

# DefiLlama path slug → 内部列名 / 中文展示
CHAINS: list[tuple[str, str, str]] = [
    ("Ethereum", "ethereum", "Ethereum"),
    ("Solana", "solana", "Solana"),
    ("Base", "base", "Base"),
    ("BSC", "bsc", "BNB Chain"),
    ("Tron", "tron", "TRON"),
    ("Monad", "monad", "Monad"),
    ("Robinhood Chain", "robinhood_chain", "Robinhood Chain"),
    ("Arbitrum", "arbitrum", "Arbitrum"),
    ("Hyperliquid L1", "hyperliquid_l1", "Hyperliquid L1"),
    ("Avalanche", "avalanche", "Avalanche"),
    ("Polygon", "polygon", "Polygon"),
    ("TON", "ton", "TON"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get_json(url: str) -> Any:
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < ATTEMPTS:
                time.sleep(min(2**attempt, 12))
    raise RuntimeError(f"GET {url} failed: {last}")


def series_to_map(rows: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = row.get("date")
        tvl = row.get("tvl")
        if not isinstance(ts, (int, float)) or not isinstance(tvl, (int, float)):
            continue
        day = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        out[day] = float(tvl)
    return out


def main() -> None:
    print("[defi-tvl] fetch global historicalChainTvl…")
    global_map = series_to_map(http_get_json(f"{API}/v2/historicalChainTvl"))
    time.sleep(SLEEP)

    chain_maps: dict[str, dict[str, float]] = {}
    for slug, key, _label in CHAINS:
        url = f"{API}/v2/historicalChainTvl/{urllib.parse.quote(slug)}"
        print(f"  chain {slug}…")
        try:
            chain_maps[key] = series_to_map(http_get_json(url))
        except Exception as exc:  # noqa: BLE001
            print(f"    WARN {slug}: {exc}")
            chain_maps[key] = {}
        time.sleep(SLEEP)

    print("[defi-tvl] fetch /v2/chains snapshot…")
    chains_now = http_get_json(f"{API}/v2/chains")
    if not isinstance(chains_now, list):
        chains_now = []
    key_by_name = {slug: key for slug, key, _ in CHAINS}
    latest_chains: list[dict[str, Any]] = []
    for item in chains_now:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        tvl = item.get("tvl")
        if not isinstance(tvl, (int, float)):
            continue
        latest_chains.append(
            {
                "name": name,
                "key": key_by_name.get(name),
                "tvl": float(tvl),
            }
        )
    latest_chains.sort(key=lambda x: x["tvl"], reverse=True)

    # 只落有全球 TVL 的完整日；Llama 分链常比全球早半日更新，避免末行只有 solana 等导致 0/图空
    all_days = sorted(global_map)
    data: list[dict[str, Any]] = []
    for day in all_days:
        g = global_map.get(day)
        if not isinstance(g, (int, float)) or g <= 0:
            continue
        row: dict[str, Any] = {"time": day, "global": float(g)}
        for key, series in chain_maps.items():
            if day in series:
                row[key] = series[day]
        data.append(row)

    payload = {
        "code": "0",
        "msg": "success",
        "name": "defi_tvl",
        "description": "全球与主力链 DeFi TVL（DefiLlama，不含流动性质押/双计）",
        "source": "defillama",
        "source_url": "https://defillama.com/chains",
        "updated_at": now_iso(),
        "chains": [{"slug": s, "key": k, "label": lab} for s, k, lab in CHAINS],
        "latest_chains": latest_chains[:40],
        "row_count": len(data),
        "data": data,
    }
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(f"[defi-tvl] wrote {OUT_PATH} rows={len(data)} latest_top={latest_chains[0]['name'] if latest_chains else '-'}")


if __name__ == "__main__":
    main()
