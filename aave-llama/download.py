"""Aave 近似月报：DefiLlama 协议 TVL / borrowed + GHO 市值。

不是 Token Terminal。父协议 TVL、借款、V4、Horizon、GHO 可对齐量级；
月活用户、全市场借贷占比没有免费字段，不写。

用法：
  python download.py
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
JSON_DIR = HERE / "output" / "json"
LATEST = JSON_DIR / "aave_llama.json"

PROTOCOLS = (
    ("aave", "Aave"),
    ("aave-v4", "Aave V4"),
    ("aave-horizon-rwa", "Horizon"),
)
GHO_URL = "https://stablecoins.llama.fi/stablecoincharts/all?stablecoin=118"
UA = {"User-Agent": "dashboard-data/aave-llama"}


def get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def day_of(ts: int | str) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()


def month_of(day: str) -> str:
    return day[:7]


def series_from_tvl(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in points:
        if not isinstance(row, dict):
            continue
        val = row.get("totalLiquidityUSD")
        if val is None or row.get("date") is None:
            continue
        out.append({"day": day_of(row["date"]), "usd": float(val)})
    out.sort(key=lambda x: x["day"])
    return out


def month_closes(daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """每个自然月取该月最后一天观测。"""
    by: dict[str, dict[str, Any]] = {}
    for row in daily:
        by[month_of(row["day"])] = row
    months = []
    for key in sorted(by):
        row = by[key]
        months.append({"month": key, "day": row["day"], "usd": row["usd"]})
    return months


def with_mom(months: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prev = None
    out = []
    for row in months:
        item = dict(row)
        if prev and prev > 0:
            item["mom_pct"] = (row["usd"] / prev - 1.0) * 100.0
        else:
            item["mom_pct"] = None
        prev = row["usd"]
        out.append(item)
    return out


def fetch_protocol(slug: str, label: str) -> dict[str, Any]:
    payload = get_json(f"https://api.llama.fi/protocol/{slug}")
    daily = series_from_tvl(payload.get("tvl") or [])
    months = with_mom(month_closes(daily))
    borrowed = (payload.get("currentChainTvls") or {}).get("borrowed")
    latest = daily[-1] if daily else {"day": None, "usd": None}
    return {
        "slug": slug,
        "label": label,
        "latest_usd": latest.get("usd"),
        "latest_day": latest.get("day"),
        "borrowed_usd": float(borrowed) if isinstance(borrowed, (int, float)) else None,
        "months": months[-36:],
    }


def fetch_gho() -> dict[str, Any]:
    payload = get_json(GHO_URL)
    daily: list[dict[str, Any]] = []
    if isinstance(payload, list):
        for row in payload:
            if not isinstance(row, dict):
                continue
            circ = ((row.get("totalCirculatingUSD") or {}).get("peggedUSD"))
            if circ is None or row.get("date") is None:
                continue
            daily.append({"day": day_of(row["date"]), "usd": float(circ)})
    daily.sort(key=lambda x: x["day"])
    months = with_mom(month_closes(daily))
    latest = daily[-1] if daily else {"day": None, "usd": None}
    ath = max((m["usd"] for m in months), default=None)
    return {
        "slug": "gho",
        "label": "GHO",
        "id": "118",
        "latest_usd": latest.get("usd"),
        "latest_day": latest.get("day"),
        "ath_usd": ath,
        "months": months[-36:],
    }


def main() -> int:
    series = [fetch_protocol(slug, label) for slug, label in PROTOCOLS]
    gho = fetch_gho()
    doc = {
        "name": "aave_llama",
        "source": "defillama",
        "source_url": "https://defillama.com/protocol/aave",
        "methodology": (
            "DefiLlama 近似口径，不是 Token Terminal。"
            "Aave/V4/Horizon 用协议日 TVL 的月末值；Aave borrowed 为当前借款余额（无历史月序列）。"
            "GHO 用稳定币 id=118 流通市值月末值。不含月活、不含全市场借贷占比。"
        ),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "series": series,
        "gho": gho,
    }
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    aave = series[0]
    print(
        f"wrote {LATEST} aave={aave['latest_usd']} borrowed={aave['borrowed_usd']} "
        f"v4={series[1]['latest_usd']} horizon={series[2]['latest_usd']} gho={gho['latest_usd']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
