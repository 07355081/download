#!/usr/bin/env python3
"""代币化国债/货基产品市值榜（CoinGecko 免费近似）。

GET /coins/markets?category=tokenized-treasuries
非 Token Terminal；名单可能缺 Franklin/IBENJI 等，合计常低于外电。

输出：output/json/tokenized_treasury_products.json
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
OUT_PATH = HERE / "output" / "json" / "tokenized_treasury_products.json"
UA = "dashboard-data/tokenized-treasury-products"
TZ_CN = timezone(timedelta(hours=8))
CATEGORY = "tokenized-treasuries"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def http_json(url: str, headers: dict[str, str]) -> Any:
    req = urllib.request.Request(
        url,
        headers={**headers, "User-Agent": UA, "Accept": "application/json"},
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
                print("GET", url.split("?")[0], "auth=" + (",".join(headers) or "none"))
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


def fetch_markets(env: dict[str, str]) -> list[dict[str, Any]]:
    params = {
        "vs_currency": "usd",
        "category": CATEGORY,
        "order": "market_cap_desc",
        "per_page": "50",
        "page": "1",
        "sparkline": "false",
    }
    doc = cg_get("/coins/markets", params, env)
    if not isinstance(doc, list) or len(doc) < 3:
        raise RuntimeError(f"markets 异常: {type(doc)} n={len(doc) if isinstance(doc, list) else None}")
    rows: list[dict[str, Any]] = []
    for item in doc:
        if not isinstance(item, dict):
            continue
        mcap = item.get("market_cap")
        if not isinstance(mcap, (int, float)) or mcap <= 0:
            continue
        rows.append(
            {
                "id": str(item.get("id") or ""),
                "symbol": str(item.get("symbol") or "").upper(),
                "name": str(item.get("name") or ""),
                "market_cap_usd": round(float(mcap), 2),
            }
        )
    rows.sort(key=lambda row: -row["market_cap_usd"])
    if len(rows) < 3:
        raise RuntimeError("有效产品不足 3 个")
    return rows


def load_prev() -> dict[str, Any]:
    if not OUT_PATH.is_file():
        return {}
    try:
        doc = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def main() -> int:
    env = load_env()
    products = fetch_markets(env)
    total = sum(row["market_cap_usd"] for row in products)
    for i, row in enumerate(products, 1):
        row["rank"] = i
        row["share_pct"] = round(100.0 * row["market_cap_usd"] / total, 2) if total else None
    day = datetime.now(TZ_CN).strftime("%Y-%m-%d")
    top3 = [row["symbol"] for row in products[:3]]
    fingerprint = "|".join(top3)

    prev = load_prev()
    history = prev.get("history") if isinstance(prev.get("history"), list) else []
    history = [row for row in history if isinstance(row, dict) and row.get("day")]
    history = [row for row in history if row.get("day") != day]
    history.append(
        {
            "day": day,
            "total_usd": round(total, 2),
            "top3": top3,
            "fingerprint": fingerprint,
            "n": len(products),
        }
    )
    history = sorted(history, key=lambda row: str(row["day"]))[-120:]

    payload = {
        "code": "0",
        "msg": "success",
        "name": "tokenized_treasury_products",
        "description": "CoinGecko category=tokenized-treasuries 产品市值榜近似。非 Token Terminal。",
        "source": "coingecko",
        "source_url": "https://www.coingecko.com/en/categories/tokenized-treasuries",
        "updated_at": now_iso(),
        "day": day,
        "latest": {
            "day": day,
            "total_usd": round(total, 2),
            "n": len(products),
            "top3": top3,
            "fingerprint": fingerprint,
            "products": products,
        },
        "history": history,
        "series": [
            {
                "label": "CoinGecko 样本合计",
                "labelEn": "CoinGecko sample total",
                "months": [{"month": row["day"], "usd": row["total_usd"]} for row in history],
            }
        ],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(
        f"wrote {OUT_PATH} day={day} n={len(products)} total={total/1e8:.2f}亿 "
        f"top3={','.join(top3)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
