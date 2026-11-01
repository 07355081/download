# -*- coding: utf-8 -*-
"""NEAR Intents TVL / 成交量（DefiLlama 免费）。

输出 output/json/near_intents.json
不占 Dune；非 dune.com/near/near-intents 官方面板口径。
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT = HERE / "output" / "json" / "near_intents.json"
UA = "wublock-near-intents/1.0"
PROTOCOL = "near-intents"
FOCUS = ("NEAR", "ZEC", "USDC", "USDT", "WBTC", "WETH")


def get(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    tvl = float(get(f"https://api.llama.fi/tvl/{PROTOCOL}"))
    proto = get(f"https://api.llama.fi/protocol/{PROTOCOL}")
    vol = get(f"https://api.llama.fi/summary/dexs/{PROTOCOL}")

    tokens_raw: dict[str, float] = {}
    series = proto.get("tokensInUsd")
    if isinstance(series, list) and series:
        last = series[-1]
        if isinstance(last, dict) and isinstance(last.get("tokens"), dict):
            for k, v in last["tokens"].items():
                try:
                    tokens_raw[str(k)] = float(v)
                except (TypeError, ValueError):
                    continue

    focus = {k: tokens_raw[k] for k in FOCUS if k in tokens_raw}
    top = sorted(tokens_raw.items(), key=lambda kv: kv[1], reverse=True)[:12]

    tvl_hist = proto.get("tvl")
    tvl_prev = None
    if isinstance(tvl_hist, list) and len(tvl_hist) >= 2:
        try:
            tvl_prev = float(tvl_hist[-2].get("totalLiquidityUSD"))
        except (TypeError, ValueError, AttributeError):
            tvl_prev = None

    payload = {
        "code": "0",
        "msg": "success",
        "source": "DefiLlama protocol/near-intents + summary/dexs",
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "name": proto.get("name") or "NEAR Intents",
        "tvl_usd": tvl,
        "tvl_prev_usd": tvl_prev,
        "chains": proto.get("currentChainTvls") or {},
        "tokens_focus_usd": focus,
        "tokens_top": [{"symbol": k, "usd": v} for k, v in top],
        "volume": {
            "total24h_usd": vol.get("total24h"),
            "total7d_usd": vol.get("total7d"),
            "total_all_usd": vol.get("totalAllTime"),
        },
        "url": "https://defillama.com/protocol/near-intents",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT)
    print(
        f"wrote {OUT} tvl={tvl/1e6:.2f}M "
        f"vol7d={(float(vol.get('total7d') or 0))/1e8:.2f}亿 "
        f"volAll={(float(vol.get('totalAllTime') or 0))/1e8:.2f}亿"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
