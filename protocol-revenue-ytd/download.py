"""固定观察名单：协议 YTD 收入榜（DefiLlama dailyRevenue 近似）。

口径：
  - GET /summary/fees/{slug}?dataType=dailyRevenue
  - 窗口：当年 1 月 1 日 → 最近已闭合 UTC 日（不含今天）
  - 不含 Tether / Circle / Grayscale（不拉、不进榜）
  - 近似 CoinGecko Learn「链上项目收入」稿，不是 CoinGecko API

输出：
  output/json/protocol_revenue_ytd.json

用法：
  python download.py
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "protocol_revenue_ytd.json"
API = "https://api.llama.fi"
UA = "dashboard-data/protocol-revenue-ytd"
TIMEOUT = 90
ATTEMPTS = 3
SLEEP = 0.3

# slug → 中文展示名（稿件用语）
WATCHLIST: list[tuple[str, str]] = [
    ("hyperliquid", "Hyperliquid"),
    ("pump", "Pump.fun"),
    ("axiom-pro", "Axiom Pro"),
    ("sky", "Sky"),
    ("gmgn", "GMGN"),
    ("polymarket", "Polymarket"),
    ("world-liberty-financial", "World Liberty Financial"),
    ("paxos", "Paxos"),
    ("edgex", "edgeX"),
    ("titan-builder", "Titan Builder"),
    ("collector-crypt", "Collector Crypt"),
    ("phantom", "Phantom"),
    ("aave", "Aave"),
    ("fomo", "fomo"),
    ("aerodrome", "Aerodrome"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get_json(url: str) -> Any:
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < ATTEMPTS:
                time.sleep(min(2**attempt, 12))
    raise RuntimeError(f"GET {url} failed: {last}")


def day_of(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def parse_daily(doc: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    chart = doc.get("totalDataChart") if isinstance(doc, dict) else None
    if not isinstance(chart, list):
        return out
    for pt in chart:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        ts, val = pt[0], pt[1]
        if isinstance(ts, (int, float)) and isinstance(val, (int, float)):
            out[day_of(int(ts))] = float(val)
    return out


def window_sum(daily: dict[str, float], start: str, end: str) -> float:
    total = 0.0
    for day, val in daily.items():
        if start <= day <= end:
            total += val
    return total


def ranking_for(
    protocols: list[dict[str, Any]], start: str, end: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in protocols:
        rev = window_sum(p.get("daily") or {}, start, end)
        rows.append(
            {
                "slug": p["slug"],
                "name": p["name"],
                "name_zh": p["name_zh"],
                "revenue": rev,
            }
        )
    rows.sort(key=lambda r: r["revenue"], reverse=True)
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    total = sum(r["revenue"] for r in rows) or 1.0
    for row in rows:
        row["share"] = row["revenue"] / total * 100.0
    return rows


def revenue_series(
    ranking: list[dict[str, Any]],
    protocols: list[dict[str, Any]],
    start: str,
    end: str,
) -> list[dict[str, Any]]:
    """页面前 5 名的日收入线。history 里的 top1_revenue 画不出来。"""
    by_slug = {p["slug"]: p for p in protocols}
    blocks: list[dict[str, Any]] = []
    for row in ranking[:5]:
        daily = (by_slug.get(row["slug"]) or {}).get("daily") or {}
        months = [
            {"month": day, "usd": float(daily[day])}
            for day in sorted(daily)
            if start <= day <= end and isinstance(daily[day], (int, float))
        ]
        if len(months) >= 2:
            blocks.append(
                {
                    "label": row.get("name_zh") or row.get("name"),
                    "labelEn": row.get("name") or row.get("name_zh"),
                    "months": months,
                }
            )
    return blocks


def main() -> None:
    today = datetime.now(timezone.utc).date()
    end = (today - timedelta(days=1)).isoformat()
    start = f"{today.year}-01-01"

    protocols: list[dict[str, Any]] = []
    for slug, name_zh in WATCHLIST:
        url = f"{API}/summary/fees/{slug}?dataType=dailyRevenue"
        print(f"[protocol-revenue-ytd] fetch {slug}…")
        try:
            doc = http_get_json(url)
        except Exception as exc:  # noqa: BLE001
            print(f"  WARN {slug}: {exc}")
            protocols.append(
                {
                    "slug": slug,
                    "name": name_zh,
                    "name_zh": name_zh,
                    "daily": {},
                    "error": str(exc),
                }
            )
            time.sleep(SLEEP)
            continue
        name = str(doc.get("displayName") or doc.get("name") or name_zh)
        daily = parse_daily(doc)
        protocols.append(
            {
                "slug": slug,
                "name": name,
                "name_zh": name_zh,
                "daily": daily,
            }
        )
        print(f"  days={len(daily)} ytd={window_sum(daily, start, end):.0f}")
        time.sleep(SLEEP)

    ranking = ranking_for(protocols, start, end)

    # 按日存 Top1/Top3，供阈值回扫（只存 slug，控制体积）
    all_days = sorted({d for p in protocols for d in (p.get("daily") or {}) if start <= d <= end})
    hist: list[dict[str, Any]] = []
    for day in all_days:
        ranks = ranking_for(protocols, start, day)
        if not ranks:
            continue
        hist.append(
            {
                "time": day,
                "top1": ranks[0]["slug"],
                "top3": [r["slug"] for r in ranks[:3]],
                "top5": [r["slug"] for r in ranks[:5]],
                "top1_revenue": ranks[0]["revenue"],
            }
        )

    out = {
        "name": "protocol_revenue_ytd",
        "source": "defillama/summary/fees?dataType=dailyRevenue",
        "note_zh": (
            "固定观察名单 YTD 协议收入（DefiLlama dailyRevenue 加总）。"
            "近似 CoinGecko 链上收入稿，不是 CoinGecko API；不含 Tether/Circle/Grayscale。"
            "Axiom 等交易终端可能与外电偏差较大。"
        ),
        "excluded": ["Tether", "Circle", "Grayscale"],
        "window_start": start,
        "window_end": end,
        "updated_at": now_iso(),
        "ranking": ranking,
        "history": hist,
        "series": revenue_series(ranking, protocols, start, end),
        "protocols": [
            {
                "slug": p["slug"],
                "name": p["name"],
                "name_zh": p["name_zh"],
                "daily": p.get("daily") or {},
                **({"error": p["error"]} if p.get("error") else {}),
            }
            for p in protocols
        ],
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    top_line = ", ".join(f"{r['name_zh']}={r['revenue']/1e8:.2f}亿" for r in ranking[:5])
    print(
        f"[protocol-revenue-ytd] wrote {OUT_PATH} window={start}..{end} "
        f"n={len(ranking)} hist={len(hist)} | {top_line}"
    )


if __name__ == "__main__":
    main()
