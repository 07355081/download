"""Arc 链上应用手续费（DefiLlama）+ 对照链，免费，不占 Dune。

口径（重要）：
  - Arc / 对照链均用 GET /overview/fees/{chain}
  - 这是「该链上各协议应用手续费合计」，不是网络 gas / Dune 那篇外电
  - 对照：Robinhood Chain、Base、Arbitrum、Sui
  - 上线首日锚点：2026-09-16（Arc 主网公开日）

输出：
  output/json/arc_app_fees.json

用法：
  python download.py
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "arc_app_fees.json"
API = "https://api.llama.fi"
UA = "dashboard-data/arc-app-fees"
TIMEOUT = 90
ATTEMPTS = 3
SLEEP = 0.35

LAUNCH_DAY = "2026-09-16"

# path slug → 内部 key / 中文名
CHAINS: list[tuple[str, str, str]] = [
    ("Arc", "arc", "Arc"),
    ("Robinhood Chain", "robinhood_chain", "Robinhood Chain"),
    ("Base", "base", "Base"),
    ("Arbitrum", "arbitrum", "Arbitrum"),
    ("Sui", "sui", "Sui"),
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


def chart_to_map(doc: Any) -> dict[str, float]:
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


def top_protocols(doc: Any, *, limit: int = 5) -> list[dict[str, Any]]:
    prots = doc.get("protocols") if isinstance(doc, dict) else None
    if not isinstance(prots, list):
        return []
    rows: list[dict[str, Any]] = []
    for p in prots:
        if not isinstance(p, dict):
            continue
        fee = p.get("total24h")
        if not isinstance(fee, (int, float)) or fee <= 0:
            continue
        rows.append(
            {
                "name": str(p.get("displayName") or p.get("name") or ""),
                "slug": str(p.get("slug") or ""),
                "fees_24h": float(fee),
            }
        )
    rows.sort(key=lambda r: r["fees_24h"], reverse=True)
    return rows[:limit]


def chain_series(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """页面只认 series[].months[].usd，认不出按链摊开的 history。"""
    blocks: list[dict[str, Any]] = []
    for _slug, key, label in CHAINS:
        months = [
            {"month": row["time"], "usd": float(row[key])}
            for row in history
            if isinstance(row.get("time"), str) and isinstance(row.get(key), (int, float))
        ]
        if len(months) >= 2:
            blocks.append({"label": label, "labelEn": label, "months": months})
    return blocks


def main() -> None:
    series: dict[str, dict[str, float]] = {}
    arc_doc: dict[str, Any] | None = None
    for slug, key, label in CHAINS:
        url = f"{API}/overview/fees/{urllib.parse.quote(slug)}"
        print(f"[arc-app-fees] fetch {label}…")
        doc = http_get_json(url)
        series[key] = chart_to_map(doc)
        if key == "arc" and isinstance(doc, dict):
            arc_doc = doc
        time.sleep(SLEEP)

    all_days = sorted({d for m in series.values() for d in m})
    history: list[dict[str, Any]] = []
    for day in all_days:
        row: dict[str, Any] = {"time": day}
        for _slug, key, _label in CHAINS:
            if day in series[key]:
                row[key] = series[key][day]
        history.append(row)

    launch_arc = series["arc"].get(LAUNCH_DAY)
    latest_day = all_days[-1] if all_days else None
    latest_arc = series["arc"].get(latest_day) if latest_day else None
    vs_launch_pct = None
    if launch_arc and launch_arc > 0 and latest_arc is not None:
        vs_launch_pct = (latest_arc - launch_arc) / launch_arc * 100.0

    top = top_protocols(arc_doc or {}, limit=5)

    out = {
        "name": "arc_app_fees",
        "source": "defillama/overview/fees/{Arc,Robinhood Chain,Base,Arbitrum,Sui}",
        "note_zh": (
            "DefiLlama 链上应用/协议手续费合计，不是网络 gas，也不是 Dune/@defioasis 外电口径。"
            f"Arc 上线首日锚点 {LAUNCH_DAY}。"
        ),
        "launch_day": LAUNCH_DAY,
        "updated_at": now_iso(),
        "as_of": latest_day,
        "latest": {
            "arc": latest_arc,
            "vs_launch_pct": vs_launch_pct,
            "launch_arc": launch_arc,
            "peers": {
                key: series[key].get(latest_day) if latest_day else None
                for _s, key, _l in CHAINS
                if key != "arc"
            },
        },
        "top_protocols_24h": top,
        "history": history,
        "series": chain_series(history),
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(
        f"[arc-app-fees] wrote {OUT_PATH} as_of={latest_day} "
        f"arc={latest_arc} vs_launch={vs_launch_pct} history={len(history)}"
    )


if __name__ == "__main__":
    main()
