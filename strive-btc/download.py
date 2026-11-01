#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strive / SpaceX / Coinbase 公开 BTC 持仓（CoinGecko companies，免费）。

口径：/companies/public_treasury/bitcoin 当前总量。
不是单笔成交、不是买入均价。

输出：
  output/json/strive_btc.json
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_PATH = HERE / "output" / "json" / "strive_btc.json"
ENV_PATH = ROOT / ".env"

GECKO = "https://api.coingecko.com/api/v3/companies/public_treasury/bitcoin"
UA = "dashboard-data/strive-btc"
TIMEOUT = 60
ATTEMPTS = 4
KEEP_DAYS = 400

# 精确公司名，避免 Semler / Gemini Space Station 误匹配
TARGETS = {
    "strive": "Strive",
    "spacex": "Space Exploration Technologies Corp.",
    "coinbase": "Coinbase Global",
}


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


def pick(companies: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_name = {str(row.get("name") or ""): row for row in companies if isinstance(row, dict)}
    out: dict[str, dict[str, Any]] = {}
    for key, name in TARGETS.items():
        row = by_name.get(name)
        if not row:
            raise RuntimeError(f"CoinGecko 财库表找不到 {name}")
        holdings = row.get("total_holdings")
        if not isinstance(holdings, (int, float)) or float(holdings) <= 0:
            raise RuntimeError(f"{name} holdings 无效: {holdings}")
        value = row.get("total_current_value_usd")
        out[key] = {
            "name": name,
            "symbol": row.get("symbol"),
            "holdings": float(holdings),
            "value_usd": float(value) if isinstance(value, (int, float)) else None,
        }
    return out


def load_existing() -> list[dict[str, Any]]:
    if not OUT_PATH.is_file():
        return []
    try:
        doc = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    data = doc.get("data") if isinstance(doc, dict) else None
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def merge_day(rows: list[dict[str, Any]], day: str, entities: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned = [row for row in rows if str(row.get("time") or "") != day]
    cleaned.append(
        {
            "time": day,
            "strive": entities["strive"]["holdings"],
            "spacex": entities["spacex"]["holdings"],
            "coinbase": entities["coinbase"]["holdings"],
            "strive_value_usd": entities["strive"]["value_usd"],
        }
    )
    cleaned.sort(key=lambda r: str(r["time"]))
    return cleaned[-KEEP_DAYS:]


def main() -> None:
    load_env(ENV_PATH)
    print("[strive-btc] fetch companies/public_treasury/bitcoin …")
    doc = http_get_json(GECKO)
    companies = doc.get("companies") if isinstance(doc, dict) else None
    if not isinstance(companies, list):
        raise RuntimeError("companies 缺失")
    entities = pick(companies)
    day = utc_day()
    data = merge_day(load_existing(), day, entities)
    print(
        f"  strive={entities['strive']['holdings']:.0f} "
        f"spacex={entities['spacex']['holdings']:.0f} "
        f"coinbase={entities['coinbase']['holdings']:.0f}"
    )
    payload = {
        "updated_at": now_iso(),
        "source": "coingecko",
        "source_url": "https://api.coingecko.com/api/v3/companies/public_treasury/bitcoin",
        "note": "CoinGecko 上市公司公开 BTC 持仓总量；不是单笔买入均价。",
        "latest": {
            "time": day,
            "entities": entities,
        },
        "data": data,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(f"[strive-btc] wrote {OUT_PATH} days={len(data)}")


if __name__ == "__main__":
    main()
