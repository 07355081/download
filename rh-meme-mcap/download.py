#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Robinhood Chain Meme 板块市值（CoinGecko categories，免费）。

口径：CoinGecko 分类 id=robinhood-chain-meme 的 market_cap。
免费接口只有当日快照，历史靠本机日更累加。

输出：
  output/json/rh_meme_mcap.json
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_PATH = HERE / "output" / "json" / "rh_meme_mcap.json"
ENV_PATH = ROOT / ".env"

CATEGORY_ID = "robinhood-chain-meme"
CATEGORY_NAME = "Robinhood Chain Meme"
GECKO = "https://api.coingecko.com/api/v3"
UA = "dashboard-data/rh-meme-mcap"
TIMEOUT = 60
ATTEMPTS = 4
TOP_N = 8


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        text = line.strip().lstrip("\ufeff")
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def gecko_key() -> str:
    return (
        os.environ.get("COINGECKO_DEMO_API_KEY")
        or os.environ.get("COINGECKO_KEY")
        or os.environ.get("COINGECKO_API_KEY")
        or ""
    ).strip()


def http_get_json(url: str) -> Any:
    headers = {"User-Agent": UA, "Accept": "application/json"}
    key = gecko_key()
    if key:
        headers["x-cg-demo-api-key"] = key
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 429 and attempt < ATTEMPTS:
                time.sleep(min(2**attempt, 20))
                continue
            if attempt < ATTEMPTS:
                time.sleep(min(2**attempt, 12))
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < ATTEMPTS:
                time.sleep(min(2**attempt, 12))
    raise RuntimeError(f"GET {url} failed: {last}")


def find_category(rows: list[dict[str, Any]]) -> dict[str, Any]:
    for row in rows:
        if str(row.get("id") or "").lower() == CATEGORY_ID:
            return row
    raise RuntimeError(f"CoinGecko categories 中找不到 {CATEGORY_ID}")


def fetch_top_coins() -> list[dict[str, Any]]:
    q = urllib.parse.urlencode(
        {
            "vs_currency": "usd",
            "category": CATEGORY_ID,
            "order": "market_cap_desc",
            "per_page": str(TOP_N),
            "page": "1",
            "sparkline": "false",
            "price_change_percentage": "24h",
        }
    )
    batch = http_get_json(f"{GECKO}/coins/markets?{q}")
    if not isinstance(batch, list):
        return []
    out: list[dict[str, Any]] = []
    for item in batch[:TOP_N]:
        if not isinstance(item, dict):
            continue
        mcap = item.get("market_cap")
        if not isinstance(mcap, (int, float)):
            continue
        ch = item.get("price_change_percentage_24h")
        if ch is None:
            ch = item.get("price_change_percentage_24h_in_currency")
        out.append(
            {
                "id": item.get("id"),
                "symbol": str(item.get("symbol") or "").upper(),
                "name": item.get("name"),
                "market_cap": float(mcap),
                "price_change_percentage_24h": float(ch)
                if isinstance(ch, (int, float))
                else None,
            }
        )
    return out


def load_existing() -> dict[str, Any]:
    if not OUT_PATH.is_file():
        return {"data": [], "latest_top": []}
    try:
        doc = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"data": [], "latest_top": []}
    if not isinstance(doc, dict):
        return {"data": [], "latest_top": []}
    data = doc.get("data")
    if not isinstance(data, list):
        data = []
    top = doc.get("latest_top")
    if not isinstance(top, list):
        top = []
    return {"data": data, "latest_top": top}


def merge_day(rows: list[dict[str, Any]], day: str, mcap: float, ch24: float | None) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("time"):
            continue
        if str(row["time"]) == day:
            continue
        cleaned.append(
            {
                "time": str(row["time"]),
                "market_cap": float(row["market_cap"])
                if isinstance(row.get("market_cap"), (int, float))
                else None,
                "market_cap_change_24h": float(row["market_cap_change_24h"])
                if isinstance(row.get("market_cap_change_24h"), (int, float))
                else None,
            }
        )
    cleaned.append({"time": day, "market_cap": mcap, "market_cap_change_24h": ch24})
    cleaned.sort(key=lambda r: str(r["time"]))
    return cleaned


def main() -> None:
    load_env(ENV_PATH)
    print("[rh-meme-mcap] fetch /coins/categories …")
    cats = http_get_json(f"{GECKO}/coins/categories?order=market_cap_desc")
    if not isinstance(cats, list):
        raise RuntimeError("categories 返回非 list")
    cat = find_category([c for c in cats if isinstance(c, dict)])
    mcap_raw = cat.get("market_cap")
    if not isinstance(mcap_raw, (int, float)) or mcap_raw <= 0:
        raise RuntimeError(f"rh-meme market_cap 无效: {mcap_raw}")
    mcap = float(mcap_raw)
    ch_raw = cat.get("market_cap_change_24h")
    ch24 = float(ch_raw) if isinstance(ch_raw, (int, float)) else None
    print(f"  category={cat.get('id')} name={cat.get('name')} mcap={mcap:.0f} ch24={ch24}")

    time.sleep(0.8)
    print("[rh-meme-mcap] fetch top markets …")
    try:
        top = fetch_top_coins()
    except Exception as exc:  # noqa: BLE001
        print(f"  WARN top markets: {exc}")
        top = []
    if top:
        bits = ", ".join(
            f"{t['symbol']}={t['market_cap']/1e8:.2f}亿"
            + (
                f"({t['price_change_percentage_24h']:+.1f}%)"
                if isinstance(t.get("price_change_percentage_24h"), (int, float))
                else ""
            )
            for t in top[:5]
        )
        print(f"  top: {bits}")

    day = utc_day()
    prev = load_existing()
    data = merge_day(prev["data"], day, mcap, ch24)
    payload = {
        "updated_at": now_iso(),
        "source": "coingecko",
        "category_id": CATEGORY_ID,
        "category_name": str(cat.get("name") or CATEGORY_NAME),
        "disclaimer": (
            "CoinGecko Robinhood Chain Meme 分类市值口径；"
            "历史序列为本机日更累加，非外电回放。"
        ),
        "latest": {
            "time": day,
            "market_cap": mcap,
            "market_cap_change_24h": ch24,
        },
        "latest_top": top,
        "data": data,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(f"[rh-meme-mcap] wrote {OUT_PATH} days={len(data)} latest={mcap/1e8:.2f}亿")


if __name__ == "__main__":
    main()
