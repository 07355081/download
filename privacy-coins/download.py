# -*- coding: utf-8 -*-
"""CoinGecko 隐私板块快照（免费/Demo key）。

主分类 privacy-coins；另拉 privacy-infrastructure，并点名 NEAR/RAIL（7d/30d）。
输出 output/json/privacy_coins.json
不占 Dune；非 Glassnode API。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "output" / "json" / "privacy_coins.json"
CATEGORY = "privacy-coins"
INFRA_CATEGORY = "privacy-infrastructure"
SPOTLIGHT = ("NEAR", "RAIL")
UA = "wublock-privacy-coins/1.0"

ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for key in ("COINGECKO_KEY", "COINGECKO_DEMO_API_KEY"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def http_json(url: str, headers: dict[str, str]) -> Any:
    req = urllib.request.Request(url, headers={**headers, "User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cg_get(path: str, params: dict[str, str], env: dict[str, str]) -> Any:
    key = (env.get("COINGECKO_KEY") or env.get("COINGECKO_DEMO_API_KEY") or "").strip()
    qs = urllib.parse.urlencode(params)
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
        try:
            return http_json(url, headers)
        except Exception as exc:  # noqa: BLE001
            last = exc
            continue
    raise RuntimeError(f"CoinGecko 失败: {last}")


def weighted_pct(rows: list[dict[str, Any]], field: str, *, exclude_symbols: set[str] | None = None) -> float | None:
    num = den = 0.0
    skip = {s.upper() for s in (exclude_symbols or set())}
    for row in rows:
        if str(row.get("symbol") or "").upper() in skip:
            continue
        try:
            mcap = float(row.get("market_cap") or 0)
            pct = row.get(field)
            if pct is None or mcap <= 0:
                continue
            num += mcap * (float(pct) / 100.0)
            den += mcap
        except (TypeError, ValueError):
            continue
    if den <= 0:
        return None
    return num / den * 100.0


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_existing() -> list[dict[str, Any]]:
    if not OUT.is_file():
        return []
    try:
        doc = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(doc, dict):
        return []
    rows = doc.get("data")
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("time"):
            continue
        mcap = row.get("market_cap")
        if not isinstance(mcap, (int, float)):
            continue
        item: dict[str, Any] = {"time": str(row["time"]), "market_cap": float(mcap)}
        ch24 = row.get("market_cap_change_24h")
        if isinstance(ch24, (int, float)):
            item["market_cap_change_24h"] = float(ch24)
        zec = row.get("zec_mcap")
        if isinstance(zec, (int, float)):
            item["zec_mcap"] = float(zec)
        share = row.get("zec_share")
        if isinstance(share, (int, float)):
            item["zec_share"] = float(share)
        out.append(item)
    return out


def merge_day(rows: list[dict[str, Any]], day: str, item: dict[str, Any]) -> list[dict[str, Any]]:
    cleaned = [row for row in rows if str(row.get("time") or "") != day]
    cleaned.append(item)
    cleaned.sort(key=lambda r: str(r["time"]))
    return cleaned


def seed_prev_day(rows: list[dict[str, Any]], day: str, mcap: float, ch24: float | None) -> list[dict[str, Any]]:
    if len(rows) >= 2 or not isinstance(ch24, (int, float)) or ch24 <= -100:
        return rows
    prev_mcap = mcap / (1.0 + ch24 / 100.0)
    if prev_mcap <= 0:
        return rows
    prev_day = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    if any(str(row.get("time") or "") == prev_day for row in rows):
        return rows
    return merge_day(rows, prev_day, {"time": prev_day, "market_cap": prev_mcap})


def pick_cat(cats: Any, cat_id: str) -> dict[str, Any] | None:
    if not isinstance(cats, list):
        return None
    return next(
        (c for c in cats if isinstance(c, dict) and str(c.get("id") or "") == cat_id),
        None,
    )


def fetch_markets(env: dict[str, str], cat_id: str) -> list[dict[str, Any]]:
    rows = cg_get(
        "/coins/markets",
        {
            "vs_currency": "usd",
            "category": cat_id,
            "order": "market_cap_desc",
            "per_page": "100",
            "page": "1",
            "price_change_percentage": "7d,30d,1y",
        },
        env,
    )
    return rows if isinstance(rows, list) else []


def coin_brief(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    try:
        mcap = float(row.get("market_cap") or 0)
    except (TypeError, ValueError):
        mcap = 0.0
    return {
        "id": row.get("id"),
        "symbol": str(row.get("symbol") or "").upper(),
        "name": row.get("name"),
        "market_cap_usd": mcap,
        "market_cap_rank": row.get("market_cap_rank"),
        "price_change_7d_pct": row.get("price_change_percentage_7d_in_currency"),
        "price_change_30d_pct": row.get("price_change_percentage_30d_in_currency"),
        "price_change_1y_pct": row.get("price_change_percentage_1y_in_currency"),
    }


def main() -> int:
    env = load_env()
    cats = cg_get("/coins/categories", {}, env)
    cat = pick_cat(cats, CATEGORY)
    if not cat:
        raise SystemExit(f"categories 里没有 {CATEGORY}")
    infra_cat = pick_cat(cats, INFRA_CATEGORY)

    markets = fetch_markets(env, CATEGORY)
    if not markets:
        raise SystemExit("markets 空")
    time.sleep(0.35)
    infra_markets = fetch_markets(env, INFRA_CATEGORY)

    btc = cg_get(
        "/coins/bitcoin",
        {
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "false",
            "developer_data": "false",
        },
        env,
    )
    md = (btc or {}).get("market_data") or {}

    total = float(cat.get("market_cap") or 0) or sum(float(x.get("market_cap") or 0) for x in markets)
    zec = next((x for x in markets if str(x.get("symbol") or "").upper() == "ZEC"), None)
    zec_mcap = float((zec or {}).get("market_cap") or 0)
    ch24 = cat.get("market_cap_change_24h")
    ch24_f = float(ch24) if isinstance(ch24, (int, float)) else None
    day = utc_day()
    today_row: dict[str, Any] = {"time": day, "market_cap": total}
    if ch24_f is not None:
        today_row["market_cap_change_24h"] = ch24_f
    if zec_mcap > 0:
        today_row["zec_mcap"] = zec_mcap
        if total > 0:
            today_row["zec_share"] = zec_mcap / total * 100.0
    data = seed_prev_day(merge_day(load_existing(), day, today_row), day, total, ch24_f)
    latest_top = [
        {
            "id": x.get("id"),
            "symbol": str(x.get("symbol") or "").upper(),
            "name": x.get("name"),
            "market_cap": float(x["market_cap"]),
        }
        for x in markets[:8]
        if isinstance(x.get("market_cap"), (int, float))
    ]

    # NEAR/RAIL：优先基础设施分类，缺失再扫宽口径 privacy 前 100（本模块只扫 infra）
    by_sym = {str(x.get("symbol") or "").upper(): x for x in infra_markets}
    for x in markets:
        sym = str(x.get("symbol") or "").upper()
        if sym in SPOTLIGHT and sym not in by_sym:
            by_sym[sym] = x
    spotlight = [coin_brief(by_sym.get(sym)) for sym in SPOTLIGHT]
    spotlight = [x for x in spotlight if x]

    infra_total = None
    if infra_cat and isinstance(infra_cat.get("market_cap"), (int, float)):
        infra_total = float(infra_cat["market_cap"])
    elif infra_markets:
        infra_total = sum(float(x.get("market_cap") or 0) for x in infra_markets)

    payload = {
        "code": "0",
        "msg": "success",
        "source": "CoinGecko privacy-coins + privacy-infrastructure",
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "category": {
            "id": CATEGORY,
            "name": cat.get("name"),
            "market_cap_usd": total,
            "market_cap_change_24h_pct": cat.get("market_cap_change_24h"),
            "volume_24h_usd": cat.get("volume_24h"),
        },
        "infrastructure": {
            "id": INFRA_CATEGORY,
            "name": (infra_cat or {}).get("name") or "Privacy Infrastructure",
            "market_cap_usd": infra_total,
            "market_cap_change_24h_pct": (infra_cat or {}).get("market_cap_change_24h"),
            "weighted_30d_pct": weighted_pct(
                infra_markets, "price_change_percentage_30d_in_currency"
            ),
            "coins_n": len(infra_markets),
        },
        "btc": {
            "ath_usd": (md.get("ath") or {}).get("usd"),
            "ath_date": (md.get("ath_date") or {}).get("usd"),
            "ath_change_pct": (md.get("ath_change_percentage") or {}).get("usd"),
            "price_usd": (md.get("current_price") or {}).get("usd"),
        },
        "zec": None
        if not zec
        else {
            "symbol": "ZEC",
            "name": zec.get("name"),
            "market_cap_usd": zec_mcap,
            "market_cap_rank": zec.get("market_cap_rank"),
            "share_pct": (zec_mcap / total * 100.0) if total > 0 else None,
            "price_change_7d_pct": zec.get("price_change_percentage_7d_in_currency"),
            "price_change_30d_pct": zec.get("price_change_percentage_30d_in_currency"),
            "price_change_1y_pct": zec.get("price_change_percentage_1y_in_currency"),
        },
        "spotlight": spotlight,
        "sector": {
            "weighted_30d_pct": weighted_pct(markets, "price_change_percentage_30d_in_currency"),
            "weighted_1y_pct": weighted_pct(markets, "price_change_percentage_1y_in_currency"),
            "ex_zec_weighted_1y_pct": weighted_pct(
                markets, "price_change_percentage_1y_in_currency", exclude_symbols={"ZEC"}
            ),
            "coins_n": len(markets),
        },
        "top": [
            {
                "symbol": x.get("symbol"),
                "market_cap_usd": x.get("market_cap"),
                "market_cap_rank": x.get("market_cap_rank"),
            }
            for x in markets[:8]
        ],
        "latest_top": latest_top,
        "data": data,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT)
    print(
        f"wrote {OUT} mcap={total/1e8:.2f}亿 infra={None if infra_total is None else round(infra_total/1e8,2)}亿 "
        f"zec_share={payload['zec']['share_pct'] if payload['zec'] else None} "
        f"spotlight={[x['symbol'] for x in spotlight]} days={len(data)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
