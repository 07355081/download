# -*- coding: utf-8 -*-
"""BNB Chain RWA 合计市值（rwa.xyz 公开页，免费）。

取 leagueTableTabs.all.networks 中 BNB Chain 一行；日更追加 history。
历史不足 2 点时用 value_7d_ago 补锚点。非 Blockworks 同源，接受口径差。
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT = HERE / "output" / "json" / "bnb_chain_rwa.json"
PAGE = "https://app.rwa.xyz/"
NETWORK_NAME = "BNB Chain"
UA = "dashboard-data/bnb-chain-rwa"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_html() -> str:
    req = urllib.request.Request(PAGE, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", "replace")


def cell_num(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("val")
    if isinstance(value, (int, float)) and value == value and value > 0:
        return float(value)
    return None


def parse_networks(html: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise RuntimeError("页面没有 __NEXT_DATA__")
    props = json.loads(match.group(1))["props"]["pageProps"]
    tabs = (props.get("leagueTableTabs") or {}).get("all") or []
    target: dict[str, Any] | None = None
    networks: list[dict[str, Any]] = []
    for tab in tabs:
        if not isinstance(tab, dict) or tab.get("key") != "networks":
            continue
        if tab.get("loginRequired"):
            continue
        for row in ((tab.get("data") or {}).get("rows") or []):
            if not isinstance(row, dict):
                continue
            name = str((row.get("group") or {}).get("name") or "").strip()
            usd = cell_num(row.get("value"))
            if not name or usd is None:
                continue
            item: dict[str, Any] = {"name": name, "usd": usd, "market_cap": usd}
            ago = cell_num(row.get("value_7d_ago"))
            if ago is not None:
                item["usd_7d_ago"] = ago
            chg = row.get("value_7d_change")
            if isinstance(chg, (int, float)):
                item["chg_7d_pct"] = float(chg) * 100.0
            share = row.get("market_share_pct")
            if isinstance(share, (int, float)):
                item["share_pct"] = float(share) * 100.0
            networks.append(item)
            if name == NETWORK_NAME:
                target = {
                    "usd": usd,
                    "usd_7d_ago": ago,
                    "chg_7d_pct": item.get("chg_7d_pct"),
                    "share_pct": item.get("share_pct"),
                }
    if target is None:
        raise RuntimeError(f"找不到 {NETWORK_NAME} 网络市值")
    networks.sort(key=lambda row: -float(row["usd"]))
    return target, networks


def load_history() -> list[dict[str, Any]]:
    if not OUT.is_file():
        return []
    try:
        doc = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = str(row.get("time") or "")[:10]
        usd = row.get("usd")
        if len(day) == 10 and isinstance(usd, (int, float)):
            out.append({"time": day, "usd": float(usd), "value": float(usd)})
    return out


def enrich(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    peak = max(float(r["usd"]) for r in rows)
    peak_day = next(r["time"] for r in rows if abs(float(r["usd"]) - peak) < 1e-6)
    out: list[dict[str, Any]] = []
    prev: float | None = None
    first = float(rows[0]["usd"])
    first_day = rows[0]["time"]
    for row in rows:
        usd = float(row["usd"])
        item: dict[str, Any] = {
            "time": row["time"],
            "usd": usd,
            "value": usd,
            "peak_usd": peak,
            "peak_day": peak_day,
            "vs_peak_pct": (usd / peak - 1.0) * 100.0 if peak else None,
        }
        if prev is not None and prev > 0:
            item["dod_pct"] = (usd / prev - 1.0) * 100.0
        if first > 0:
            item["vs_start_pct"] = (usd / first - 1.0) * 100.0
            item["start_day"] = first_day
            item["start_usd"] = first
        out.append(item)
        prev = usd
    return out


def main() -> int:
    snap, networks = parse_networks(fetch_html())
    usd = float(snap["usd"])
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    hist = {r["time"]: r for r in load_history()}
    hist[day] = {"time": day, "usd": usd, "value": usd}

    ago = snap.get("usd_7d_ago")
    if isinstance(ago, (int, float)) and ago > 0 and len(hist) < 2:
        day7 = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
        if day7 not in hist:
            hist[day7] = {
                "time": day7,
                "usd": float(ago),
                "value": float(ago),
                "seed": "value_7d_ago",
            }

    rows = enrich([hist[k] for k in sorted(hist)])
    latest = rows[-1]
    if isinstance(snap.get("chg_7d_pct"), (int, float)):
        latest["chg_7d_pct"] = round(float(snap["chg_7d_pct"]), 2)
    if isinstance(ago, (int, float)):
        latest["usd_7d_ago"] = float(ago)
    if isinstance(snap.get("share_pct"), (int, float)):
        latest["share_pct"] = round(float(snap["share_pct"]), 2)

    doc = {
        "name": "bnb_chain_rwa",
        "source": "rwa.xyz all networks",
        "source_url": PAGE,
        "note": "BNB Chain 全口径 RWA 合计（公开页 networks）。非 Blockworks；历史不足时用 value_7d_ago 作锚点。",
        "updated_at": now_iso(),
        "as_of": day,
        "latest": latest,
        "data": rows,
        "networks": networks,
        "latest_top": [
            {"symbol": row["name"], "name": row["name"], "market_cap": round(float(row["usd"]), 2)}
            for row in networks[:12]
        ],
        "meta": {
            "network": NETWORK_NAME,
            "as_of": day,
            "updated_at": now_iso(),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT)
    print(
        f"[bnb-chain-rwa] day={day} usd={usd/1e8:.2f}亿 "
        f"peak={float(latest['peak_usd'])/1e8:.2f}亿 vs_peak={latest.get('vs_peak_pct'):.2f}% "
        f"n={len(rows)} → {OUT}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
