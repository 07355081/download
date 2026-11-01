# -*- coding: utf-8 -*-
"""USDT vs ETH 市值（CoinGecko）。用于 USDT 市值超过 ETH。

顺带读看板 DefiLlama all_usdt，不对齐也不挡。
输出：output/json/usdt_eth_rank.json
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "output" / "json" / "usdt_eth_rank.json"
UA = "dashboard-data/usdt-eth-rank"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
LLAMA_CANDIDATES = [
    ROOT / "stablecoin" / "output" / "json" / "stablecoins_marketcap.json",
    Path("/root/dashboard/public/json/stablecoin/stablecoins_marketcap.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "stablecoin" / "stablecoins_marketcap.json",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("COINGECKO_KEY", "COINGECKO_DEMO_API_KEY", "COINGECKO_API_KEY"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def http_json(url: str, headers: dict[str, str]) -> Any:
    req = urllib.request.Request(
        url, headers={**headers, "User-Agent": UA, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cg_markets(env: dict[str, str]) -> list[dict[str, Any]]:
    params = {
        "vs_currency": "usd",
        "ids": "bitcoin,ethereum,tether",
        "order": "market_cap_desc",
    }
    qs = urllib.parse.urlencode(params)
    key = (
        env.get("COINGECKO_KEY")
        or env.get("COINGECKO_DEMO_API_KEY")
        or env.get("COINGECKO_API_KEY")
        or ""
    ).strip()
    attempts: list[tuple[str, dict[str, str]]] = []
    if key:
        attempts.append(
            (f"https://api.coingecko.com/api/v3/coins/markets?{qs}", {"x-cg-demo-api-key": key})
        )
        attempts.append(
            (f"https://pro-api.coingecko.com/api/v3/coins/markets?{qs}", {"x-cg-pro-api-key": key})
        )
    attempts.append((f"https://api.coingecko.com/api/v3/coins/markets?{qs}", {}))
    last: Exception | None = None
    for url, headers in attempts:
        try:
            rows = http_json(url, headers)
            if isinstance(rows, list) and rows:
                return [row for row in rows if isinstance(row, dict)]
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise RuntimeError(f"CoinGecko markets 失败: {last}")


def llama_usdt() -> dict[str, Any] | None:
    for path in LLAMA_CANDIDATES:
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        rows = doc.get("data") if isinstance(doc, dict) else None
        if not isinstance(rows, list) or not rows:
            continue
        last = rows[-1] if isinstance(rows[-1], dict) else None
        if not last or last.get("all_usdt") is None:
            continue
        return {"day": last.get("time"), "usdt_usd": float(last["all_usdt"])}
    return None


def pick(rows: list[dict[str, Any]], coin_id: str) -> dict[str, Any]:
    row = next((item for item in rows if item.get("id") == coin_id), None)
    if not row:
        raise RuntimeError(f"CoinGecko 无 {coin_id}")
    mcap = float(row.get("market_cap") or 0)
    if mcap <= 0:
        raise RuntimeError(f"{coin_id} 市值为空")
    return {
        "id": coin_id,
        "symbol": str(row.get("symbol") or "").upper(),
        "name": row.get("name"),
        "mcap_usd": mcap,
        "rank": int(row["market_cap_rank"]) if row.get("market_cap_rank") else None,
        "price_usd": float(row["current_price"]) if row.get("current_price") is not None else None,
    }


def main() -> None:
    rows = cg_markets(load_env())
    btc = pick(rows, "bitcoin")
    eth = pick(rows, "ethereum")
    usdt = pick(rows, "tether")
    llama = llama_usdt()
    payload = {
        "updated_at": now_iso(),
        "asof_day": utc_day(),
        "source": "CoinGecko coins/markets bitcoin,ethereum,tether",
        "note": "排名与比较用 CoinGecko。看板 DefiLlama all_usdt 仅对照。非 The Block。",
        "btc": btc,
        "eth": eth,
        "usdt": usdt,
        "usdt_over_eth": usdt["mcap_usd"] > eth["mcap_usd"],
        "llama_usdt": llama,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[usdt-eth-rank] USDT={usdt['mcap_usd']/1e8:.1f}亿#{usdt['rank']} "
        f"ETH={eth['mcap_usd']/1e8:.1f}亿#{eth['rank']} "
        f"over={payload['usdt_over_eth']} → {OUT}"
    )


if __name__ == "__main__":
    main()
