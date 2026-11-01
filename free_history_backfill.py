#!/usr/bin/env python3
"""用 DefiLlama 免费历史曲线，补手续费分布和应用收入里已经删掉的日子。

只跑一次，不进每日任务。已有日文件不覆盖。单条链失败就跳过。
重算不出的日子不写文件。
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SLEEP = 0.8
TIMEOUT = 90
MIN_FEE_CHAINS = 10
MIN_REVENUE_CHAINS = 5
TZ_CN = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def shanghai_today() -> str:
    return datetime.now(TZ_CN).date().isoformat()


def http_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "wublock-data-free-backfill/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("返回不是对象")
    return payload


def chart_map(payload: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for point in payload.get("totalDataChart") or []:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        ts, value = point[0], point[1]
        if not isinstance(ts, (int, float)) or not isinstance(value, (int, float)):
            continue
        day = datetime.fromtimestamp(int(ts), timezone.utc).date().isoformat()
        out[day] = float(value)
    return out


def existing_days(history_dir: Path) -> set[str]:
    if not history_dir.is_dir():
        return set()
    return {path.stem for path in history_dir.glob("????-??-??.json")}


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_cached_chart(path: Path, url: str) -> dict[str, float]:
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = None
        if isinstance(raw, dict) and isinstance(raw.get("series"), dict):
            return {str(day): float(value) for day, value in raw["series"].items() if isinstance(value, (int, float))}
    series: dict[str, float] = {}
    delay = 15
    for attempt in range(5):
        try:
            series = chart_map(http_json(url))
            break
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            print(f"    skip {url}: {text}")
            if "429" not in text and "Too Many" not in text:
                break
            time.sleep(delay)
            delay = min(delay * 2, 120)
            series = {}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"series": series}, ensure_ascii=False), encoding="utf-8")
    time.sleep(SLEEP)
    return series


def backfill_chain_fees() -> None:
    here = ROOT / "chain-fees-dist"
    history = here / "output" / "json" / "chain_fees_dist_history"
    cache = here / "output" / "cache" / "summary_fees"
    overview = http_json(
        "https://api.llama.fi/overview/fees"
        "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyFees"
    )
    chains: list[tuple[str, str]] = []
    for item in overview.get("protocols") or []:
        if not isinstance(item, dict):
            continue
        if item.get("protocolType") != "chain" and item.get("category") != "Chain" and not str(item.get("defillamaId") or "").startswith("chain#"):
            continue
        slug = str(item.get("slug") or "").strip()
        name = str(item.get("displayName") or item.get("name") or slug).strip()
        if slug and name:
            chains.append((slug, name))
    print(f"[chain-fees] chains={len(chains)}")
    series_by_slug: dict[str, tuple[str, dict[str, float]]] = {}
    for slug, name in chains:
        url = "https://api.llama.fi/summary/fees/" + urllib.parse.quote(slug, safe=".-") + "?dataType=dailyFees"
        series_by_slug[slug] = (name, load_cached_chart(cache / f"{slug}.json", url))
    have = existing_days(history)
    today = shanghai_today()
    days = sorted({day for _, series in series_by_slug.values() for day in series if day < today and day not in have})
    written = 0
    for day in days:
        rows = []
        for slug, (name, series) in series_by_slug.items():
            fees_usd = series.get(day)
            if fees_usd is None or fees_usd != fees_usd:
                continue
            rows.append({"name": name, "fees_usd": fees_usd, "slug": slug})
        if len(rows) < MIN_FEE_CHAINS:
            continue
        rows.sort(key=lambda row: row["fees_usd"], reverse=True)
        top = [
            {"name": row["name"], "fees_usd": round(row["fees_usd"], 2), "slug": row["slug"]}
            for row in rows
            if row["fees_usd"] >= 100_000
        ]
        write_json(
            history / f"{day}.json",
            {
                "code": "0",
                "msg": "success",
                "name": "chain_fees_dist",
                "description": "公链日手续费长尾分档。由各链 summary/fees 历史曲线重算，不是当日 overview 快照。",
                "source": "defillama",
                "backfill": "defillama-summary-fees",
                "updated_at": now_iso(),
                "day": day,
                "universe": len(rows),
                "lt_1k": sum(1 for row in rows if row["fees_usd"] < 1_000),
                "lt_10": sum(1 for row in rows if row["fees_usd"] < 10),
                "eq_0": sum(1 for row in rows if row["fees_usd"] == 0),
                "ge_100k": len(top),
                "top": top,
            },
        )
        written += 1
    print(f"[chain-fees] wrote {written} missing days")


def backfill_chain_revenue() -> None:
    here = ROOT / "chain-app-revenue"
    history = here / "output" / "json" / "chain_app_revenue_history"
    cache = here / "output" / "cache" / "overview_revenue"
    overview = http_json(
        "https://api.llama.fi/overview/fees"
        "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyRevenue"
    )
    names = [str(name) for name in overview.get("allChains") or [] if str(name).strip()]
    print(f"[chain-revenue] chains={len(names)}")
    series_by_name: dict[str, dict[str, float]] = {}
    for name in names:
        if name.strip().lower() in {"all", "off_chain", "offchain", "off-chain"}:
            continue
        slug = urllib.parse.quote(name, safe=".-")
        url = (
            "https://api.llama.fi/overview/fees/"
            + slug
            + "?excludeTotalDataChart=false&excludeTotalDataChartBreakdown=true&dataType=dailyRevenue"
        )
        safe_file = "".join(ch if ch.isalnum() else "_" for ch in name)[:80] or "chain"
        series_by_name[name] = load_cached_chart(cache / f"{safe_file}.json", url)
    have = existing_days(history)
    today = shanghai_today()
    days = sorted({day for series in series_by_name.values() for day in series if day < today and day not in have})
    written = 0
    for day in days:
        ranked = []
        for name, series in series_by_name.items():
            amount = series.get(day)
            if amount is None or amount <= 0:
                continue
            chain_id = name.strip().lower().replace(" ", "-")
            ranked.append((chain_id, name, amount))
        if len(ranked) < MIN_REVENUE_CHAINS:
            continue
        ranked.sort(key=lambda item: item[2], reverse=True)
        chains = [
            {"id": chain_id, "name": name, "revenue_usd": round(amount, 2), "rank": index}
            for index, (chain_id, name, amount) in enumerate(ranked, start=1)
        ]
        write_json(
            history / f"{day}.json",
            {
                "code": "0",
                "msg": "success",
                "name": "chain_app_revenue",
                "description": "各链应用收入。由分链 overview/fees dailyRevenue 历史曲线重算，不是当日 breakdown 快照。",
                "source": "defillama",
                "backfill": "defillama-chain-overview",
                "updated_at": now_iso(),
                "day": day,
                "chain_count": len(chains),
                "top": chains[:12],
                "chains": chains,
            },
        )
        written += 1
    print(f"[chain-revenue] wrote {written} missing days")


def main() -> None:
    for name, fn in (("chain-fees", backfill_chain_fees), ("chain-revenue", backfill_chain_revenue)):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            print(f"[{name}] 回补失败，其它数据不动：{exc}")


if __name__ == "__main__":
    main()
