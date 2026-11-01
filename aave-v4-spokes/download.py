#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Aave V4 存款/借款 + Spoke 结构（免费）。

- DefiLlama aave-v4：协议 TVL / 借款 / 分链（近邻）
- Aave 官方 GraphQL api.v4.aave.com：protocolHistory 存款/借款序列、hubs、spokes

不是 JackMandin 专有口径；数字可能与外电 2 亿/6000 万差一个数量级。

用法：
  python download.py
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
JSON_DIR = HERE / "output" / "json"
LATEST = JSON_DIR / "aave_v4_spokes.json"
GQL_URL = "https://api.v4.aave.com/graphql"
LLAMA_URL = "https://api.llama.fi/protocol/aave-v4"
UA = {"User-Agent": "dashboard-data/aave-v4-spokes"}

# 主要主网；chains 查询失败时回退
FALLBACK_CHAIN_IDS = [1, 10, 56, 100, 137, 8453, 42161, 43114, 59144, 534352]


def http_json(url: str, payload: dict[str, Any] | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = dict(UA)
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def gql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"query": query}
    if variables is not None:
        body["variables"] = variables
    doc = http_json(GQL_URL, body)
    if doc.get("errors"):
        raise RuntimeError(doc["errors"][0].get("message") or doc["errors"])
    data = doc.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("GraphQL 无 data")
    return data


def fnum(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def fetch_chain_ids() -> list[int]:
    try:
        data = gql("{ chains { name chainId isTestnet isFork } }")
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError):
        return list(FALLBACK_CHAIN_IDS)
    out: list[int] = []
    for row in data.get("chains") or []:
        if not isinstance(row, dict):
            continue
        if row.get("isTestnet") or row.get("isFork"):
            continue
        cid = fnum(row.get("chainId"))
        if cid is None:
            continue
        out.append(int(cid))
    return out or list(FALLBACK_CHAIN_IDS)


def fetch_protocol_history() -> list[dict[str, Any]]:
    data = gql(
        """
        query {
          protocolHistory(request: { currency: USD, window: ALL }) {
            date
            deposits { value }
            borrows { value }
            availableLiquidity { value }
            utilizationRate { value }
          }
        }
        """
    )
    rows: list[dict[str, Any]] = []
    for item in data.get("protocolHistory") or []:
        if not isinstance(item, dict):
            continue
        day = str(item.get("date") or "")[:10]
        dep = fnum((item.get("deposits") or {}).get("value"))
        bor = fnum((item.get("borrows") or {}).get("value"))
        if not day or dep is None:
            continue
        rows.append(
            {
                "day": day,
                "deposits_usd": round(dep, 2),
                "borrows_usd": round(bor, 2) if bor is not None else None,
                "available_usd": round(fnum((item.get("availableLiquidity") or {}).get("value")) or 0, 2),
                "util": fnum((item.get("utilizationRate") or {}).get("value")),
            }
        )
    rows.sort(key=lambda x: x["day"])
    return rows


def fetch_hubs(chain_ids: list[int]) -> list[dict[str, Any]]:
    data = gql(
        """
        query($c: [ChainId!]!) {
          hubs(request: { query: { chainIds: $c }, orderBy: { totalSupplied: DESC } }) {
            id
            name
            address
            displayName
            displayTitle
            chain { name chainId }
            summary {
              totalSupplied { current { value } change { value } }
              totalBorrowed { current { value } change { value } }
              utilizationRate { value }
            }
          }
        }
        """,
        {"c": chain_ids},
    )
    out: list[dict[str, Any]] = []
    for h in data.get("hubs") or []:
        if not isinstance(h, dict):
            continue
        summary = h.get("summary") or {}
        supplied = fnum(((summary.get("totalSupplied") or {}).get("current") or {}).get("value")) or 0.0
        borrowed = fnum(((summary.get("totalBorrowed") or {}).get("current") or {}).get("value")) or 0.0
        chain = h.get("chain") or {}
        out.append(
            {
                "id": h.get("id"),
                "name": h.get("displayName") or h.get("name"),
                "title": h.get("displayTitle"),
                "address": h.get("address"),
                "chain": chain.get("name"),
                "chain_id": int(fnum(chain.get("chainId")) or 0),
                "supplied_usd": round(supplied, 2),
                "borrowed_usd": round(borrowed, 2),
                "util": fnum((summary.get("utilizationRate") or {}).get("value")),
            }
        )
    out.sort(key=lambda x: -x["supplied_usd"])
    return out


def fetch_spokes(chain_ids: list[int]) -> list[dict[str, Any]]:
    data = gql(
        """
        query($c: [ChainId!]!) {
          spokes(request: { query: { chainIds: $c } }) {
            id
            name
            address
            displayName
            displayTitle
            chain { name chainId }
            summary {
              totalSupplied { value }
              totalBorrowed { value }
              uniqueAssets
              connectedHubs
            }
          }
        }
        """,
        {"c": chain_ids},
    )
    out: list[dict[str, Any]] = []
    for s in data.get("spokes") or []:
        if not isinstance(s, dict):
            continue
        summary = s.get("summary") or {}
        supplied = fnum((summary.get("totalSupplied") or {}).get("value")) or 0.0
        borrowed = fnum((summary.get("totalBorrowed") or {}).get("value")) or 0.0
        chain = s.get("chain") or {}
        name = s.get("displayName") or s.get("name") or ""
        out.append(
            {
                "id": s.get("id"),
                "name": name,
                "title": s.get("displayTitle"),
                "address": s.get("address"),
                "chain": chain.get("name"),
                "chain_id": int(fnum(chain.get("chainId")) or 0),
                "supplied_usd": round(supplied, 2),
                "borrowed_usd": round(borrowed, 2),
                "unique_assets": summary.get("uniqueAssets"),
                "connected_hubs": summary.get("connectedHubs"),
            }
        )
    out.sort(key=lambda x: -x["supplied_usd"])
    return out


def fetch_llama() -> dict[str, Any]:
    payload = http_json(LLAMA_URL)
    chain_tvls = payload.get("currentChainTvls") or {}
    borrowed = fnum(chain_tvls.get("borrowed"))
    # 去掉 borrowed / staking 等非链键，算分链 TVL
    chain_usd: dict[str, float] = {}
    for key, val in chain_tvls.items():
        if not isinstance(key, str):
            continue
        if key.lower() in {"borrowed", "staking", "pool2", "vesting", "offers", "treasury"}:
            continue
        if "-" in key and key.split("-")[-1].lower() in {"borrowed", "staking"}:
            continue
        num = fnum(val)
        if num is None or num <= 0:
            continue
        chain_usd[key] = float(num)
    total = sum(chain_usd.values())
    eth = chain_usd.get("Ethereum") or chain_usd.get("ethereum") or 0.0
    tvl_series = payload.get("tvl") or []
    latest_tvl = None
    latest_day = None
    if tvl_series:
        last = tvl_series[-1]
        latest_tvl = fnum(last.get("totalLiquidityUSD"))
        ts = last.get("date")
        if ts is not None:
            latest_day = datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
    return {
        "tvl_usd": latest_tvl,
        "tvl_day": latest_day,
        "borrowed_usd": borrowed,
        "eth_tvl_usd": eth if eth else None,
        "eth_share_pct": round(eth / total * 100.0, 2) if total > 0 and eth else None,
        "chains": [
            {"chain": k, "usd": round(v, 2), "share_pct": round(v / total * 100.0, 2) if total else None}
            for k, v in sorted(chain_usd.items(), key=lambda kv: -kv[1])
        ],
    }


def summarize(
    history: list[dict[str, Any]],
    hubs: list[dict[str, Any]],
    spokes: list[dict[str, Any]],
    llama: dict[str, Any],
) -> dict[str, Any]:
    latest = history[-1] if history else {}
    deps = [float(r["deposits_usd"]) for r in history if r.get("deposits_usd") is not None]
    ath = max(deps) if deps else None
    ath_day = None
    if ath is not None:
        for row in history:
            if float(row["deposits_usd"]) >= ath - 1e-6:
                ath_day = row["day"]
    deposits = fnum(latest.get("deposits_usd"))
    borrows = fnum(latest.get("borrows_usd"))

    eth_hub = sum(h["supplied_usd"] for h in hubs if str(h.get("chain") or "").lower() == "ethereum")
    hub_total = sum(h["supplied_usd"] for h in hubs) or 0.0
    eth_share_hub = round(eth_hub / hub_total * 100.0, 2) if hub_total > 0 else None
    # 相对协议存款（与外电「超 2/3」同口径近邻），勿用「Hub 内占比」以免偏高
    eth_share_of_deposits = (
        round(eth_hub / deposits * 100.0, 2) if deposits and deposits > 0 and eth_hub else None
    )

    def pick(name_sub: str) -> dict[str, Any] | None:
        key = name_sub.lower()
        for s in spokes:
            if key in str(s.get("name") or "").lower():
                return s
        return None

    named = {
        "bluechip": pick("bluechip"),
        "etherfi": pick("etherfi"),
        "forex": pick("forex"),
        "gold": pick("gold"),
        "paxg": pick("paxg"),
    }
    forex_gold = 0.0
    for key in ("forex", "gold", "paxg"):
        item = named.get(key)
        if item:
            forex_gold += float(item.get("supplied_usd") or 0)

    chg_7d = None
    if len(history) >= 8 and deposits is not None:
        prev = fnum(history[-8].get("deposits_usd"))
        if prev and prev > 0:
            chg_7d = round((deposits / prev - 1.0) * 100.0, 2)

    return {
        "asof_day": latest.get("day"),
        "deposits_usd": deposits,
        "borrows_usd": borrows,
        "deposits_ath_usd": ath,
        "deposits_ath_day": ath_day,
        "deposits_is_ath": bool(
            deposits is not None and ath is not None and deposits >= ath - 1.0
        ),
        "deposits_chg_7d_pct": chg_7d,
        "eth_share_hub_pct": eth_share_hub,
        "eth_share_of_deposits_pct": eth_share_of_deposits,
        "eth_share_llama_pct": llama.get("eth_share_pct"),
        "named_spokes": {
            k: (
                {
                    "name": v["name"],
                    "supplied_usd": v["supplied_usd"],
                    "borrowed_usd": v["borrowed_usd"],
                    "chain": v["chain"],
                }
                if v
                else None
            )
            for k, v in named.items()
        },
        "forex_gold_supplied_usd": round(forex_gold, 2),
        "top_spokes": spokes[:8],
    }


def main() -> int:
    chain_ids = fetch_chain_ids()
    history = fetch_protocol_history()
    hubs = fetch_hubs(chain_ids)
    spokes = fetch_spokes(chain_ids)
    llama = fetch_llama()
    if not history:
        raise RuntimeError("protocolHistory 为空")
    snap = summarize(history, hubs, spokes, llama)
    doc = {
        "name": "aave_v4_spokes",
        "source": "aave-graphql+defillama",
        "source_urls": {
            "graphql": GQL_URL,
            "llama": "https://defillama.com/protocol/aave-v4",
        },
        "methodology": (
            "存款/借款日序列与 Spoke/Hub 来自 Aave 官方 GraphQL（免费）；"
            "DefiLlama aave-v4 作 TVL/分链近邻。"
            "不是 JackMandin；外电 2 亿/6000 万量级不对齐时以本通道为准。"
        ),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "chain_ids": chain_ids,
        "latest": snap,
        "history": history[-400:],
        "hubs": hubs,
        "spokes": spokes,
        "llama": llama,
    }
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LATEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(LATEST)
    print(
        f"wrote {LATEST} day={snap.get('asof_day')} "
        f"dep={snap.get('deposits_usd')} bor={snap.get('borrows_usd')} "
        f"ath={snap.get('deposits_is_ath')} eth_hub%={snap.get('eth_share_hub_pct')} "
        f"spokes={len(spokes)} hubs={len(hubs)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
