# -*- coding: utf-8 -*-
"""DeFi 协议自然月收入榜（DefiLlama dailyRevenue，免费）。

- 从 overview 取近两段收入靠前的协议，并带上同一 parent 的子产品
- 日收入按 parent 合并（Pump、Hyperliquid、Circle 等）
- 只发布已结束的 UTC 自然月 Top 15 及环比

输出：
  output/json/protocol_revenue_monthly.json
  cache/charts.json

用法：
  python download.py
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from calendar import monthrange
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
JSON_DIR = HERE / "output" / "json"
CACHE_PATH = HERE / "cache" / "charts.json"
OUT_PATH = JSON_DIR / "protocol_revenue_monthly.json"

API = "https://api.llama.fi"
OVERVIEW = (
    f"{API}/overview/fees"
    "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyRevenue"
)
UA = "dashboard-data/protocol-revenue-monthly"
TIMEOUT = 90
ATTEMPTS = 3
SLEEP = 0.15
TOP_N = 15
PICK = 45
NAME_OVERRIDES = {
    "parent#pump": "Pump",
    "parent#hyperliquid": "Hyperliquid",
    "parent#circle": "Circle",
    "parent#uniswap": "Uniswap",
    "slug:tether": "Tether",
}


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


def parse_daily(doc: Any) -> dict[str, float]:
    chart = doc.get("totalDataChart") if isinstance(doc, dict) else None
    out: dict[str, float] = {}
    if not isinstance(chart, list):
        return out
    for pt in chart:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        ts, val = pt[0], pt[1]
        if isinstance(ts, (int, float)) and isinstance(val, (int, float)):
            day = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
            out[day] = float(val)
    return out


def load_cache() -> dict[str, dict[str, float]]:
    if not CACHE_PATH.is_file():
        return {}
    try:
        doc = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    charts = doc.get("charts") if isinstance(doc, dict) else None
    return charts if isinstance(charts, dict) else {}


def save_cache(charts: dict[str, dict[str, float]]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": now_iso(), "charts": charts}
    tmp = CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(CACHE_PATH)


def score(p: dict[str, Any]) -> float:
    a = p.get("total30d")
    b = p.get("total60dto30d")
    vals = [float(x) for x in (a, b) if isinstance(x, (int, float))]
    return max(vals) if vals else 0.0


def group_key(p: dict[str, Any]) -> str:
    parent = str(p.get("parentProtocol") or "").strip()
    if parent:
        return parent
    return f"slug:{p.get('slug')}"


def display_name(key: str, members: list[dict[str, Any]]) -> str:
    if key in NAME_OVERRIDES:
        return NAME_OVERRIDES[key]
    best = max(members, key=score)
    name = str(best.get("displayName") or best.get("name") or key)
    return name


def month_sums(daily: dict[str, float]) -> dict[str, tuple[float, int]]:
    out: dict[str, list[float]] = {}
    for day, val in daily.items():
        ym = day[:7]
        bucket = out.setdefault(ym, [0.0, 0])
        bucket[0] += val
        bucket[1] += 1
    return {ym: (total, int(days)) for ym, (total, days) in out.items()}


def closed_months(today: datetime, month_keys: set[str]) -> list[str]:
    """日曲线里已经有的完整月，不另发请求。当月未结束，不计入。"""
    current = today.strftime("%Y-%m")
    return sorted(ym for ym in month_keys if ym < current)


def is_complete(ym: str, days: int) -> bool:
    y, m = int(ym[:4]), int(ym[5:7])
    return days >= monthrange(y, m)[1]


def revenue_lines(months_out: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not months_out:
        return []
    latest_top = months_out[-1].get("top") or []
    series: list[dict[str, Any]] = []
    for row in latest_top[:5]:
        key = row.get("key")
        name = str(row.get("name") or key or "")
        points = []
        for month in months_out:
            hit = next((item for item in (month.get("top") or []) if item.get("key") == key), None)
            if hit and isinstance(hit.get("revenue_usd"), (int, float)):
                points.append({"month": month["month"], "usd": hit["revenue_usd"]})
        if len(points) >= 2 and name:
            series.append({"label": name, "labelEn": name, "months": points})
    return series


def main() -> int:
    print("[protocol-revenue-monthly] overview…")
    overview = http_get_json(OVERVIEW)
    protocols = overview.get("protocols") if isinstance(overview, dict) else None
    if not isinstance(protocols, list):
        print("[ERROR] overview missing protocols")
        return 1

    ranked = sorted(
        (p for p in protocols if isinstance(p, dict) and p.get("slug")),
        key=score,
        reverse=True,
    )
    picked_slugs = {str(p["slug"]) for p in ranked[:PICK]}
    parents = {
        str(p.get("parentProtocol"))
        for p in ranked[:PICK]
        if p.get("parentProtocol")
    }
    selected = [
        p
        for p in ranked
        if str(p.get("slug")) in picked_slugs or str(p.get("parentProtocol") or "") in parents
    ]
    print(f"  selected={len(selected)} from top {PICK} plus siblings")

    cache = load_cache()
    charts: dict[str, dict[str, float]] = {}
    for p in selected:
        slug = str(p["slug"])
        cached = cache.get(slug)
        if isinstance(cached, dict) and cached:
            charts[slug] = {str(k): float(v) for k, v in cached.items() if isinstance(v, (int, float))}
            # 缓存覆盖到昨天附近就跳过
            last = max(charts[slug]) if charts[slug] else ""
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if last >= today:
                continue
            # 仍拉一次，用新图覆盖（协议图不长，保证月末完整）
        url = f"{API}/summary/fees/{urllib.parse.quote(slug)}?dataType=dailyRevenue"
        try:
            doc = http_get_json(url)
            daily = parse_daily(doc)
        except Exception as exc:  # noqa: BLE001
            print(f"  WARN {slug}: {exc}")
            daily = charts.get(slug) or {}
        if daily:
            charts[slug] = daily
        time.sleep(SLEEP)
    save_cache(charts)

    groups: dict[str, list[dict[str, Any]]] = {}
    for p in selected:
        groups.setdefault(group_key(p), []).append(p)

    group_months: dict[str, dict[str, tuple[float, int]]] = {}
    names: dict[str, str] = {}
    for key, members in groups.items():
        names[key] = display_name(key, members)
        merged: dict[str, float] = {}
        for m in members:
            for day, val in (charts.get(str(m["slug"])) or {}).items():
                merged[day] = merged.get(day, 0.0) + val
        group_months[key] = month_sums(merged)

    today = datetime.now(timezone.utc)
    month_keys: set[str] = set()
    for by_m in group_months.values():
        month_keys.update(by_m)
    months_out: list[dict[str, Any]] = []
    prev_map: dict[str, float] = {}
    for ym in closed_months(today, month_keys):
        rows = []
        for key, by_m in group_months.items():
            total, days = by_m.get(ym, (0.0, 0))
            if not is_complete(ym, days) and total <= 0:
                continue
            if not is_complete(ym, days):
                continue
            prev = prev_map.get(key)
            delta = None if prev is None else total - prev
            pct = None if prev in (None, 0) else (total / prev - 1.0) * 100.0
            rows.append(
                {
                    "key": key,
                    "name": names[key],
                    "revenue_usd": round(total, 2),
                    "mom_usd": None if delta is None else round(delta, 2),
                    "mom_pct": None if pct is None else round(pct, 4),
                }
            )
        if len(rows) < 5:
            prev_map = {r["key"]: r["revenue_usd"] for r in rows} or prev_map
            continue
        rows.sort(key=lambda r: r["revenue_usd"], reverse=True)
        top = rows[:TOP_N]
        for i, row in enumerate(top, 1):
            row["rank"] = i
        gainers = sum(1 for r in top if isinstance(r.get("mom_usd"), (int, float)) and r["mom_usd"] > 0)
        months_out.append(
            {
                "month": ym,
                "top": top,
                "ranked_count": len(rows),
                "top15_gainers": gainers,
            }
        )
        prev_map = {r["key"]: r["revenue_usd"] for r in rows}

    latest = months_out[-1] if months_out else None
    payload = {
        "code": "0",
        "msg": "success",
        "name": "protocol_revenue_monthly",
        "description": "协议自然月收入 Top15（DefiLlama dailyRevenue，按 parent 合并）",
        "source": "defillama",
        "source_url": "https://defillama.com/fees",
        "note": (
            "按 UTC 自然月加总 dailyRevenue，同一 parentProtocol 的子产品合并。"
            "含稳定币发行方（Tether、Circle）。与 DeFiLlama 推文的名单和月份切法可能仍有差异，"
            "发稿写 DefiLlama dailyRevenue。"
        ),
        "updated_at": now_iso(),
        "series": revenue_lines(months_out),
        "selected_protocols": len(selected),
        "latest_month": None if latest is None else latest["month"],
        "months": months_out,
    }
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    if latest:
        head = ", ".join(
            f"{r['name']}={r['revenue_usd']/1e6:.1f}M" for r in latest["top"][:5]
        )
        print(
            f"[protocol-revenue-monthly] wrote {OUT_PATH} months={len(months_out)} "
            f"latest={latest['month']} gainers={latest['top15_gainers']} | {head}"
        )
    else:
        print(f"[protocol-revenue-monthly] wrote {OUT_PATH} but no closed month")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
