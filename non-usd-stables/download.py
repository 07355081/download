#!/usr/bin/env python3
"""非美元稳定币流通供应近似（DefiLlama）。

1) GET /stablecoins?includePrices=true 取 pegType≠peggedUSD 的币
2) 按现价折美元，取累计覆盖约 95% 的头部币（上限 20）
3) 拉各币 stablecoincharts/all?stablecoin={id}，按日加总 totalCirculatingUSD
4) 另计 peggedEUR 占当日非美元合计的供应份额（不是交易活动占比）

不含月成交、持币地址。无 Dune。

输出：output/json/non_usd_stables.json
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "non_usd_stables.json"
LIST_URL = "https://stablecoins.llama.fi/stablecoins?includePrices=true"
CHART_URL = "https://stablecoins.llama.fi/stablecoincharts/all"
UA = "dashboard-data/non-usd-stables"
TIMEOUT = 90
SLEEP = 0.35
ATTEMPTS = 4
COVERAGE = 0.95
MAX_COINS = 20
MIN_USD = 1_000_000.0


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_json(url: str) -> Any:
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < ATTEMPTS:
                time.sleep(min(2**attempt, 12))
                continue
            raise
    raise RuntimeError(f"GET failed: {last}")


def coin_usd(asset: dict[str, Any]) -> float:
    circ = asset.get("circulating") or {}
    if not isinstance(circ, dict) or not circ:
        return 0.0
    native = float(next(iter(circ.values())) or 0.0)
    price = float(asset.get("price") or 0.0) or 1.0
    return native * price


def pick_coins(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[tuple[float, dict[str, Any]]] = []
    for asset in assets:
        peg = str(asset.get("pegType") or "")
        if peg in ("", "peggedUSD"):
            continue
        usd = coin_usd(asset)
        if usd < MIN_USD:
            continue
        rows.append((usd, asset))
    rows.sort(key=lambda item: -item[0])
    total = sum(usd for usd, _ in rows)
    if total <= 0:
        raise RuntimeError("非美元稳定币合计为 0")
    picked: list[dict[str, Any]] = []
    cum = 0.0
    for usd, asset in rows:
        picked.append(
            {
                "id": str(asset.get("id")),
                "symbol": str(asset.get("symbol") or ""),
                "name": str(asset.get("name") or ""),
                "pegType": str(asset.get("pegType") or ""),
                "spot_usd": round(usd, 2),
            }
        )
        cum += usd
        if cum / total >= COVERAGE or len(picked) >= MAX_COINS:
            break
    return picked


def day_of(raw: Any) -> str | None:
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw), timezone.utc).strftime("%Y-%m-%d")
    text = str(raw or "").strip()
    if text.isdigit():
        return datetime.fromtimestamp(int(text), timezone.utc).strftime("%Y-%m-%d")
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None


def chart_usd_by_day(coin_id: str) -> dict[str, float]:
    doc = http_json(f"{CHART_URL}?stablecoin={coin_id}")
    if not isinstance(doc, list):
        raise RuntimeError(f"coin {coin_id} chart 非数组")
    out: dict[str, float] = {}
    for row in doc:
        if not isinstance(row, dict):
            continue
        day = day_of(row.get("date"))
        blob = row.get("totalCirculatingUSD")
        if not day or not isinstance(blob, dict):
            continue
        usd = sum(float(v) for v in blob.values() if isinstance(v, (int, float)))
        if usd > 0:
            out[day] = usd
    return out


def build() -> dict[str, Any]:
    print("GET", LIST_URL)
    listed = http_json(LIST_URL)
    assets = listed.get("peggedAssets") if isinstance(listed, dict) else None
    if not isinstance(assets, list):
        raise RuntimeError("stablecoins 列表异常")
    coins = pick_coins(assets)
    print(f"picked {len(coins)} coins:", ", ".join(c["symbol"] for c in coins))

    total_by: dict[str, float] = defaultdict(float)
    eur_by: dict[str, float] = defaultdict(float)
    for i, coin in enumerate(coins):
        print(f"  chart {coin['symbol']} id={coin['id']}")
        series = chart_usd_by_day(coin["id"])
        for day, usd in series.items():
            total_by[day] += usd
            if coin["pegType"] == "peggedEUR":
                eur_by[day] += usd
        if i + 1 < len(coins):
            time.sleep(SLEEP)

    days = []
    prev_total: float | None = None
    for day in sorted(total_by):
        total = float(total_by[day])
        eur = float(eur_by.get(day, 0.0))
        share = round(100.0 * eur / total, 2) if total > 0 else None
        dod = None
        if prev_total and prev_total > 0:
            dod = round(100.0 * (total - prev_total) / prev_total, 2)
        days.append(
            {
                "day": day,
                "total_usd": round(total, 2),
                "eur_usd": round(eur, 2),
                "eur_share_pct": share,
                "total_dod_pct": dod,
            }
        )
        prev_total = total

    if len(days) < 30:
        raise RuntimeError(f"日序列过短: {len(days)}")

    return {
        "code": "0",
        "msg": "success",
        "name": "non_usd_stables",
        "description": "DefiLlama 非美元稳定币头部供应合计（USD）。EUR 为供应份额，非交易活动占比。",
        "source": "defillama-stablecoins",
        "source_url": "https://defillama.com/stablecoins",
        "updated_at": now_iso(),
        "coverage": COVERAGE,
        "coins": coins,
        "day_count": len(days),
        "days": days,
        "series": [
            {
                "label": "非美元合计",
                "labelEn": "Non-USD total",
                "months": [{"month": row["day"], "usd": row["total_usd"]} for row in days],
            },
            {
                "label": "欧元稳定币",
                "labelEn": "EUR-pegged",
                "months": [{"month": row["day"], "usd": row["eur_usd"]} for row in days],
            },
        ],
    }


def main() -> int:
    payload = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    latest = payload["days"][-1]
    print(
        f"wrote {OUT_PATH} days={payload['day_count']} "
        f"latest={latest['day']} total={latest['total_usd']/1e8:.2f}亿 "
        f"eur_share={latest.get('eur_share_pct')}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
