#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代币化股票市值（CoinGecko 免费口径）。

- category=tokenized-stock 分页合计总市值
- 分链：优先 coins/list platforms；失败则按名称启发式（bstock→BNB、xstock→Solana、ondo→ETH）
- 非 Token Terminal；数字可能低于外电约 39 亿

用法：
  python download.py
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_DIR = HERE / "output" / "json"
LATEST = JSON_DIR / "tokenized_stocks_mcap.json"
PER_PAGE = 50
MAX_PAGES = 8
SLEEP_S = 1.4
TZ_CN = timezone(timedelta(hours=8))
UA = "dashboard-data/tokenized-stocks-mcap"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

CHAIN_LABEL = {
    "bnb": "BNB Chain",
    "ethereum": "Ethereum",
    "solana": "Solana",
    "robinhood": "Robinhood Chain",
    "base": "Base",
    "arbitrum": "Arbitrum",
    "avalanche": "Avalanche",
    "other": "其他",
    "unknown": "未标注",
}

PLATFORM_MAP = {
    "binance-smart-chain": "bnb",
    "binancecoin": "bnb",
    "ethereum": "ethereum",
    "solana": "solana",
    "base": "base",
    "arbitrum-one": "arbitrum",
    "arbitrum": "arbitrum",
    "avalanche": "avalanche",
    "polygon-pos": "other",
    "optimistic-ethereum": "other",
}


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


def cg_key(env: dict[str, str]) -> str:
    return (
        env.get("COINGECKO_KEY")
        or env.get("COINGECKO_DEMO_API_KEY")
        or env.get("COINGECKO_API_KEY")
        or ""
    ).strip()


def http_json(url: str, headers: dict[str, str] | None = None) -> Any:
    req = urllib.request.Request(
        url,
        headers={**(headers or {}), "User-Agent": UA, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cg_get(path: str, params: dict[str, Any], env: dict[str, str]) -> Any:
    qs = urllib.parse.urlencode(params)
    key = cg_key(env)
    attempts: list[tuple[str, dict[str, str]]] = []
    if key:
        attempts.append(
            (f"https://api.coingecko.com/api/v3{path}?{qs}", {"x-cg-demo-api-key": key})
        )
        attempts.append(
            (f"https://pro-api.coingecko.com/api/v3{path}?{qs}", {"x-cg-pro-api-key": key})
        )
    attempts.append((f"https://api.coingecko.com/api/v3{path}?{qs}", {}))
    last: Exception | None = None
    for url, headers in attempts:
        for attempt in range(4):
            try:
                return http_json(url, headers)
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code in (429, 503) and attempt < 3:
                    time.sleep(3.0 * (attempt + 1))
                    continue
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last = exc
                break
    raise RuntimeError(f"CoinGecko {path} 失败: {last}")


def guess_chain(cid: str, symbol: str, name: str) -> str:
    s = f"{cid} {symbol} {name}".lower()
    if "bstock" in s or "bstocks" in s or re.search(r"\bbsc\b", s):
        return "bnb"
    if "xstock" in s or "xstocks" in s:
        return "solana"
    if "ondo" in s:
        return "ethereum"
    if "robinhood" in s or re.search(r"\brh[-_]", s):
        return "robinhood"
    if "dinari" in s or "dshare" in s:
        return "ethereum"
    if "backedu" in s:
        return "solana"
    return "other"


def platforms_to_chain(platforms: dict[str, Any]) -> str | None:
    keys = [k for k, v in platforms.items() if v]
    if not keys:
        return None
    mapped = [PLATFORM_MAP.get(k, "other") for k in keys]
    # 多链：均分前在调用方处理；这里取主链偏好
    for prefer in ("bnb", "ethereum", "solana", "robinhood", "base"):
        if prefer in mapped:
            return prefer
    return mapped[0]


def fetch_markets(env: dict[str, str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        batch = cg_get(
            "/coins/markets",
            {
                "vs_currency": "usd",
                "category": "tokenized-stock",
                "order": "market_cap_desc",
                "per_page": PER_PAGE,
                "page": page,
                "sparkline": "false",
            },
            env,
        )
        if not isinstance(batch, list) or not batch:
            break
        out.extend([row for row in batch if isinstance(row, dict)])
        if len(batch) < PER_PAGE:
            break
        time.sleep(SLEEP_S)
    return out


def fetch_platforms(env: dict[str, str], ids: set[str]) -> dict[str, dict[str, Any]]:
    try:
        rows = cg_get("/coins/list", {"include_platform": "true"}, env)
    except Exception as exc:  # noqa: BLE001
        print(f"[tokenized-stocks-mcap] platforms skip: {exc}")
        return {}
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("id") or "")
        if cid in ids:
            plats = row.get("platforms")
            out[cid] = plats if isinstance(plats, dict) else {}
    return out


def category_mcap(env: dict[str, str]) -> float | None:
    try:
        cats = cg_get("/coins/categories", {}, env)
    except Exception as exc:  # noqa: BLE001
        print(f"[tokenized-stocks-mcap] categories skip: {exc}")
        return None
    if not isinstance(cats, list):
        return None
    for row in cats:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").lower()
        cid = str(row.get("id") or "").lower()
        if "tokenized stock" in name or cid in ("tokenized-stock", "tokenized-stocks"):
            try:
                return float(row.get("market_cap") or 0) or None
            except (TypeError, ValueError):
                return None
    return None


def load_prev() -> dict[str, Any]:
    if not LATEST.is_file():
        return {}
    try:
        doc = json.loads(LATEST.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def main() -> int:
    env = load_env()
    markets = fetch_markets(env)
    if not markets:
        print("[ERROR] 未拉到 tokenized-stock 列表", flush=True)
        return 1

    ids = {str(m.get("id") or "") for m in markets if m.get("id")}
    plats = fetch_platforms(env, ids)
    time.sleep(0.5)
    cat_mcap = category_mcap(env)

    chain_mcap: dict[str, float] = defaultdict(float)
    method = "platforms" if plats else "name_heuristic"
    multi = 0
    top_assets: list[dict[str, Any]] = []

    for m in markets:
        mcap = float(m.get("market_cap") or 0)
        if mcap <= 0:
            continue
        cid = str(m.get("id") or "")
        symbol = str(m.get("symbol") or "")
        name = str(m.get("name") or "")
        platforms = plats.get(cid) or {}
        chain_keys: list[str]
        if platforms:
            keys = [k for k, v in platforms.items() if v]
            mapped = [PLATFORM_MAP.get(k, "other") for k in keys] or ["unknown"]
            # 去重保序
            seen: set[str] = set()
            chain_keys = []
            for c in mapped:
                if c not in seen:
                    seen.add(c)
                    chain_keys.append(c)
            if len(chain_keys) > 1:
                multi += 1
                share = mcap / len(chain_keys)
                for c in chain_keys:
                    chain_mcap[c] += share
            else:
                chain_mcap[chain_keys[0]] += mcap
            primary = chain_keys[0]
        else:
            primary = guess_chain(cid, symbol, name)
            chain_mcap[primary] += mcap
        if len(top_assets) < 12:
            top_assets.append(
                {
                    "id": cid,
                    "symbol": symbol,
                    "name": name,
                    "mcap_usd": mcap,
                    "chain": primary,
                    "chain_label": CHAIN_LABEL.get(primary, primary),
                }
            )

    sum_mcap = sum(float(m.get("market_cap") or 0) for m in markets)
    total = float(cat_mcap) if cat_mcap and cat_mcap > 0 else sum_mcap
    day = datetime.now(TZ_CN).strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    ranked = sorted(chain_mcap.items(), key=lambda kv: -kv[1])
    chains = [
        {
            "id": cid,
            "label": CHAIN_LABEL.get(cid, cid),
            "mcap_usd": round(val, 2),
            "share_pct": round(100.0 * val / total, 2) if total > 0 else None,
        }
        for cid, val in ranked
        if val > 0
    ]
    top3 = chains[:3]
    top3_share = sum(float(c["share_pct"] or 0) for c in top3) if top3 else 0.0

    prev = load_prev()
    history = [h for h in (prev.get("history") or []) if isinstance(h, dict)]
    history = [h for h in history if str(h.get("day")) != day]
    history.append(
        {
            "day": day,
            "mcap_usd": round(total, 2),
            "n_assets": len(markets),
            "bnb_usd": round(chain_mcap.get("bnb", 0.0), 2),
            "ethereum_usd": round(chain_mcap.get("ethereum", 0.0), 2),
            "solana_usd": round(chain_mcap.get("solana", 0.0), 2),
            "top3_share_pct": round(top3_share, 2),
        }
    )
    prior = [float(h["mcap_usd"]) for h in history if str(h.get("day")) != day and isinstance(h.get("mcap_usd"), (int, float))]
    prior_high = max(prior) if prior else None
    prev_day = None
    for h in reversed(history):
        if str(h.get("day")) != day and isinstance(h.get("mcap_usd"), (int, float)):
            prev_day = float(h["mcap_usd"])
            break
    dod_pct = None
    if prev_day and prev_day > 0:
        dod_pct = 100.0 * (total - prev_day) / prev_day
    is_ath = prior_high is None or total >= prior_high - 1e-6

    latest = {
        "asof_day": day,
        "mcap_usd": round(total, 2),
        "mcap_sum_listed_usd": round(sum_mcap, 2),
        "category_mcap_usd": round(cat_mcap, 2) if cat_mcap else None,
        "n_assets": len(markets),
        "chain_method": method,
        "multi_chain_assets": multi,
        "chains": chains,
        "top3_share_pct": round(top3_share, 2),
        "top_assets": top_assets,
        "mcap_is_ath": bool(is_ath and prior_high is not None),
        "prior_high_usd": round(prior_high, 2) if prior_high is not None else None,
        "dod_pct": round(dod_pct, 2) if dod_pct is not None else None,
    }

    doc = {
        "updated_at": now_iso,
        "source": "CoinGecko coins/markets category=tokenized-stock",
        "note": "非 Token Terminal；分链为 platforms 或名称启发式近似",
        "latest": latest,
        "history": history,
        "data": [{"day": h["day"], "mcap_usd": h["mcap_usd"]} for h in history],
    }
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LATEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(LATEST)
    print(
        f"[tokenized-stocks-mcap] day={day} total=${total/1e9:.2f}B "
        f"n={len(markets)} method={method} ath={latest['mcap_is_ath']} dod={dod_pct}"
    )
    for c in chains[:5]:
        print(f"  {c['label']}: ${c['mcap_usd']/1e6:.1f}M ({c['share_pct']}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
