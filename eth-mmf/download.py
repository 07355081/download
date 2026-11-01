"""Ethereum 上代币化货基市值。公开 rwa.xyz 资产页，不占付费额度。

只记 BENJI / BUIDL（对照外电），并带上 WTGXX、iBENJI 以免误称最大。
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "eth_mmf.json"
UA = "WuBlock-Dashboard/eth-mmf (https://data.wublock123.com)"
ASSETS = (
    {"slug": "BENJI", "ticker": "BENJI", "manager": "Franklin Templeton"},
    {"slug": "BUIDL", "ticker": "BUIDL", "manager": "BlackRock"},
    {"slug": "WTGXX", "ticker": "WTGXX", "manager": "WisdomTree"},
    {"slug": "iBENJI", "ticker": "iBENJI", "manager": "Franklin Templeton"},
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(url: str) -> str:
    last: Exception | None = None
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < 3:
                time.sleep(2**attempt)
    raise RuntimeError(f"GET {url} failed: {last}")


def num(blob: Any, key: str = "val") -> float | None:
    if isinstance(blob, (int, float)):
        return float(blob)
    if isinstance(blob, dict) and isinstance(blob.get(key), (int, float)):
        return float(blob[key])
    return None


def parse_asset(html: str, meta: dict[str, str]) -> dict[str, Any]:
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise RuntimeError(f"{meta['slug']} 没有 NEXT_DATA")
    asset = json.loads(match.group(1))["props"]["pageProps"].get("asset") or {}
    nav = 1.0
    eth = 0.0
    eth_30d = None
    networks: list[dict[str, Any]] = []
    for tok in asset.get("tokens") or []:
        if not isinstance(tok, dict):
            continue
        net = str((tok.get("network") or {}).get("name") or tok.get("network_name") or "")
        supply = num(tok.get("raw_supply"))
        if supply is None:
            continue
        usd = supply * nav
        row = {"network": net, "usd": round(usd, 2)}
        chg = num(tok.get("raw_supply"), "chg_30d_pct")
        if chg is not None:
            row["chg_30d_pct"] = round(chg, 4)
        networks.append(row)
        if net == "Ethereum":
            eth += usd
            if chg is not None:
                eth_30d = (0.0 if eth_30d is None else eth_30d) + usd * chg / 100.0
    if eth <= 0:
        raise RuntimeError(f"{meta['slug']} 没有 Ethereum 市值")
    total = num(asset.get("raw_supply")) or sum(item["usd"] for item in networks)
    out: dict[str, Any] = {
        "ticker": meta["ticker"],
        "name": str(asset.get("name") or meta["ticker"]),
        "manager": meta["manager"],
        "source": f"https://app.rwa.xyz/assets/{meta['slug']}",
        "nav": nav,
        "total_usd": round(float(total), 2),
        "eth_usd": round(eth, 2),
        "networks": networks,
    }
    if eth_30d is not None and eth > 0:
        out["eth_chg_30d_pct"] = round(eth_30d / eth * 100.0, 4)
    total_chg = num(asset.get("raw_supply"), "chg_30d_pct")
    if total_chg is not None:
        out["total_chg_30d_pct"] = round(total_chg, 4)
    return out


def main() -> None:
    funds: list[dict[str, Any]] = []
    for meta in ASSETS:
        print(f"[eth-mmf] fetch {meta['slug']}…")
        funds.append(parse_asset(fetch(f"https://app.rwa.xyz/assets/{meta['slug']}"), meta))
        time.sleep(0.35)
    by_ticker = {row["ticker"]: row for row in funds}
    benji = by_ticker["BENJI"]["eth_usd"]
    buidl = by_ticker["BUIDL"]["eth_usd"]
    ranked = sorted(funds, key=lambda row: -row["eth_usd"])
    snap = {
        "name": "eth_mmf",
        "note_zh": "rwa.xyz 公开页。Ethereum 链上市值，NAV 按 1 美元。只比较 BENJI 与 BUIDL 的领先关系。",
        "updated_at": now_iso(),
        "benji_leads": benji > buidl,
        "leader": "BENJI" if benji > buidl else "BUIDL",
        "top_eth": ranked[0]["ticker"],
        "funds": funds,
        "latest_top": [
            {"symbol": row["ticker"], "name": row["name"], "market_cap": row["total_usd"]}
            for row in ranked
            if isinstance(row.get("total_usd"), (int, float))
        ],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(
        f"[eth-mmf] wrote {OUT_PATH} BENJI={benji:.0f} BUIDL={buidl:.0f} "
        f"leader={snap['leader']} top={snap['top_eth']}"
    )


if __name__ == "__main__":
    main()
