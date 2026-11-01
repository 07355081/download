# -*- coding: utf-8 -*-
"""CoinGecko Privacy Coins 板块快照（免费/Demo key）。

输出 output/json/privacy_coins.json
不占 Dune；非 Glassnode API。
"""
from __future__ import annotations

import json
import os
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


def main() -> int:
    env = load_env()
    cats = cg_get("/coins/categories", {}, env)
    cat = next(
        (c for c in cats if isinstance(c, dict) and str(c.get("id") or "") == CATEGORY),
        None,
    )
    if not cat:
        raise SystemExit(f"categories 里没有 {CATEGORY}")

    markets = cg_get(
        "/coins/markets",
        {
            "vs_currency": "usd",
            "category": CATEGORY,
            "order": "market_cap_desc",
            "per_page": "100",
            "page": "1",
            "price_change_percentage": "30d,1y",
        },
        env,
    )
    if not isinstance(markets, list) or not markets:
        raise SystemExit("markets 空")

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
    payload = {
        "code": "0",
        "msg": "success",
        "source": "CoinGecko category=privacy-coins",
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "category": {
            "id": CATEGORY,
            "name": cat.get("name"),
            "market_cap_usd": total,
            "market_cap_change_24h_pct": cat.get("market_cap_change_24h"),
            "volume_24h_usd": cat.get("volume_24h"),
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
            "price_change_30d_pct": zec.get("price_change_percentage_30d_in_currency"),
            "price_change_1y_pct": zec.get("price_change_percentage_1y_in_currency"),
        },
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
        f"wrote {OUT} mcap={total/1e8:.2f}亿 zec_share="
        f"{payload['zec']['share_pct'] if payload['zec'] else None} "
        f"btc_ath_chg={payload['btc']['ath_change_pct']} days={len(data)} top={len(latest_top)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
