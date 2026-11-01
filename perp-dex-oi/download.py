"""拉取 DefiLlama Perp / Open Interest 概览并落盘（免费，不占 Dune）。

口径：
  - 源：GET https://api.llama.fi/overview/open-interest
  - 只加总 category 为 Derivatives 的协议（不含预测市场、期权、Interface）
  - 焦点协议：Hyperliquid Perps / Aster Perps / Variational

输出：
  output/json/perp_dex_oi.json

用法：
  python download.py
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
JSON_DIR = HERE / "output" / "json"
OUT_PATH = JSON_DIR / "perp_dex_oi.json"

API = "https://api.llama.fi/overview/open-interest"
UA = "dashboard-data/perp-dex-oi"
TIMEOUT = 90
ATTEMPTS = 3

# 展示与历史序列用的焦点协议（DefiLlama name / slug）
FOCUS: list[tuple[str, str, str]] = [
    ("hyperliquid_perps", "Hyperliquid Perps", "hyperliquid-perps"),
    ("aster_perps", "Aster Perps", "aster-perps"),
    ("variational", "Variational", "variational"),
]

# 名字里带这些的，即使被标成 Derivatives 也不进合计（期权、预测市场误分类）
EXCLUDE_NAME_SUBSTR = ("kalshi", "polymarket", "option", "prediction")
DERIVATIVES_CATEGORY = "Derivatives"


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


def is_excluded(name: str, category: str = "", slug: str = "") -> bool:
    if (category or "").strip() != DERIVATIVES_CATEGORY:
        return True
    blob = f"{name} {slug}".lower()
    return any(s in blob for s in EXCLUDE_NAME_SUBSTR)


def protocol_oi(p: dict[str, Any]) -> float:
    for key in ("total24h", "totalOpenInterest", "openInterest", "openInterestAtEnd"):
        val = p.get(key)
        if isinstance(val, (int, float)) and float(val) > 0:
            return float(val)
    return 0.0


def build_snapshot(doc: dict[str, Any]) -> dict[str, Any]:
    protocols_raw = doc.get("protocols") or []
    if not isinstance(protocols_raw, list):
        protocols_raw = []

    rows: list[dict[str, Any]] = []
    include_names: set[str] = set()
    for p in protocols_raw:
        if not isinstance(p, dict):
            continue
        name = str(p.get("displayName") or p.get("name") or "").strip()
        slug = str(p.get("slug") or "").strip()
        category = str(p.get("category") or "").strip()
        if not name or is_excluded(name, category, slug):
            continue
        oi = protocol_oi(p)
        if oi <= 0:
            continue
        include_names.add(name)
        rows.append(
            {
                "name": name,
                "slug": slug,
                "category": category,
                "protocol_type": str(p.get("protocolType") or ""),
                "oi": oi,
            }
        )
    rows.sort(key=lambda x: x["oi"], reverse=True)
    total = sum(r["oi"] for r in rows)

    top: list[dict[str, Any]] = []
    for r in rows[:20]:
        top.append(
            {
                **r,
                "share": (r["oi"] / total * 100.0) if total > 0 else 0.0,
            }
        )

    by_slug = {r["slug"]: r for r in rows}
    by_name = {r["name"]: r for r in rows}
    focus: list[dict[str, Any]] = []
    for key, name, slug in FOCUS:
        hit = by_slug.get(slug) or by_name.get(name)
        oi = float(hit["oi"]) if hit else 0.0
        focus.append(
            {
                "key": key,
                "name": name,
                "slug": slug,
                "oi": oi,
                "share": (oi / total * 100.0) if total > 0 else 0.0,
                "rank": next((i + 1 for i, r in enumerate(rows) if r["name"] == name or r["slug"] == slug), None),
            }
        )

    # 历史：焦点三协议 + 过滤后合计（按当日 breakdown 里仍属于 include_names 的键求和）
    breakdown = doc.get("totalDataChartBreakdown") or []
    history: list[dict[str, Any]] = []
    focus_names = {name: key for key, name, _slug in FOCUS}
    if isinstance(breakdown, list):
        for item in breakdown:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            ts, mapping = item[0], item[1]
            if not isinstance(ts, (int, float)) or not isinstance(mapping, dict):
                continue
            day = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
            row: dict[str, Any] = {"time": day}
            day_total = 0.0
            for pname, val in mapping.items():
                if not isinstance(val, (int, float)):
                    continue
                # 历史日用「当前仍纳入名单」的名字；焦点协议始终计入
                if pname in focus_names or pname in include_names:
                    day_total += float(val)
                if pname in focus_names:
                    row[focus_names[pname]] = float(val)
            row["total_oi"] = day_total
            history.append(row)
    history.sort(key=lambda r: str(r["time"]))

    as_of = history[-1]["time"] if history else datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 对外展示的合计 / 焦点占比以历史 breakdown 末日为准（与日序列一致）。
    # 实时 protocols 列表可能含 breakdown 未拆出的 Interface，合计会偏大。
    display_total = total
    if history:
        last = history[-1]
        display_total = float(last.get("total_oi") or 0) or total
        for item in focus:
            key = str(item["key"])
            oi = float(last.get(key) or item["oi"] or 0)
            item["oi"] = oi
            item["share"] = (oi / display_total * 100.0) if display_total > 0 else 0.0
        for item in top:
            item["share"] = (
                float(item["oi"]) / display_total * 100.0 if display_total > 0 else 0.0
            )

    return {
        "name": "perp_dex_oi",
        "source": "defillama/overview/open-interest",
        "note_zh": (
            "链上永续 DEX 未平仓只加总 DefiLlama open-interest 里类别为衍生品的协议，"
            "不含预测市场、期权和 Interface。合计与焦点占比按日度 breakdown 对齐。"
        ),
        "updated_at": now_iso(),
        "as_of": as_of,
        "total_oi": display_total,
        "total_oi_live_protocols": total,
        "protocol_count": len(rows),
        "focus": focus,
        "top": top,
        "history": history,
        "series": oi_series(history),
    }


def oi_series(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """页面认不出 total_oi / 协议列，补一条可画的序列。"""
    specs = [("total_oi", "未平仓合计", "Total OI")] + [
        (key, name, name) for key, name, _slug in FOCUS
    ]
    blocks: list[dict[str, Any]] = []
    for key, label, label_en in specs:
        months = [
            {"month": row["time"], "usd": float(row[key])}
            for row in history
            if isinstance(row.get("time"), str) and isinstance(row.get(key), (int, float))
        ]
        if len(months) >= 2:
            blocks.append({"label": label, "labelEn": label_en, "months": months})
    return blocks


def main() -> None:
    print("[perp-dex-oi] fetch overview/open-interest…")
    doc = http_get_json(API)
    if not isinstance(doc, dict):
        raise RuntimeError("unexpected response type")
    snap = build_snapshot(doc)
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    focus_line = ", ".join(
        f"{f['name']}={f['oi']/1e8:.2f}亿({f['share']:.1f}%)" for f in snap["focus"]
    )
    print(
        f"[perp-dex-oi] wrote {OUT_PATH} as_of={snap['as_of']} "
        f"total={snap['total_oi']/1e8:.2f}亿 n={snap['protocol_count']} "
        f"history={len(snap['history'])} | {focus_line}"
    )


if __name__ == "__main__":
    main()
