# -*- coding: utf-8 -*-
"""加密协议近一年收入：去掉稳定币发行商和公链（DefiLlama，免费）。

口径：overview/fees?dataType=dailyRevenue，按协议 category 加总 total1y。
排除 Stablecoin Issuer、Chain。另记衍生品、Launchpad、预测市场、Telegram Bot。
没有同比序列，不写同比增长。日更只追加一个截面点。
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT = HERE / "output" / "json" / "protocol_revenue_ex.json"
API = (
    "https://api.llama.fi/overview/fees"
    "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true"
    "&dataType=dailyRevenue"
)
UA = "dashboard-data/protocol-revenue-ex"
SKIP = {"Stablecoin Issuer", "Chain"}
FOCUS = (
    ("Derivatives", "衍生品", "Derivatives"),
    ("Launchpad", "Launchpad", "Launchpad"),
    ("Prediction Market", "预测市场", "Prediction markets"),
    ("Telegram Bot", "Telegram Bot", "Telegram bots"),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def num(value: Any) -> float:
    if isinstance(value, (int, float)) and value == value:
        return float(value)
    return 0.0


def fetch() -> list[dict[str, Any]]:
    last: Exception | None = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(API, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                doc = json.loads(resp.read().decode("utf-8"))
            rows = doc.get("protocols") if isinstance(doc, dict) else None
            if not isinstance(rows, list):
                raise RuntimeError("overview 没有 protocols")
            return [row for row in rows if isinstance(row, dict)]
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(min(2**attempt, 12))
    raise RuntimeError(f"DefiLlama overview 失败: {last}")


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


def main() -> int:
    protocols = fetch()
    year = 0.0
    d30 = 0.0
    prev30 = 0.0
    focus = {key: {"y": 0.0, "d30": 0.0} for key, _, _ in FOCUS}
    n_kept = 0
    for row in protocols:
        cat = str(row.get("category") or "")
        if cat in SKIP:
            continue
        y = num(row.get("total1y"))
        a = num(row.get("total30d"))
        b = num(row.get("total60dto30d"))
        year += y
        d30 += a
        prev30 += b
        n_kept += 1
        if cat in focus:
            focus[cat]["y"] += y
            focus[cat]["d30"] += a

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    chg = (d30 / prev30 - 1.0) * 100.0 if prev30 > 0 else None
    hist = {row["time"]: row for row in load_history()}
    hist[day] = {"time": day, "usd": year, "value": year}
    rows = [hist[k] for k in sorted(hist)]
    latest: dict[str, Any] = {
        "time": day,
        "usd": year,
        "value": year,
        "usd_30d": d30,
        "usd_prev_30d": prev30,
        "protocols": n_kept,
    }
    if chg is not None:
        latest["chg_30d_pct"] = round(chg, 2)

    top = []
    for key, zh, en in FOCUS:
        top.append(
            {
                "name": zh,
                "nameEn": en,
                "category": key,
                "amount": round(focus[key]["y"], 2),
                "usd_30d": round(focus[key]["d30"], 2),
            }
        )
    top.sort(key=lambda row: -float(row["amount"]))

    doc = {
        "name": "protocol_revenue_ex",
        "source": "DefiLlama overview/fees dailyRevenue",
        "source_url": "https://defillama.com/revenue",
        "note": "近一年协议收入，已排除 Stablecoin Issuer 与 Chain。不是同比，也不是 SeaLaunch 原文。",
        "updated_at": now_iso(),
        "as_of": day,
        "latest": latest,
        "data": rows,
        "top": top,
        "meta": {"excluded": sorted(SKIP), "protocols": n_kept, "as_of": day},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT)
    print(
        f"[protocol-revenue-ex] day={day} y={year/1e8:.2f}亿 "
        f"d30={d30/1e8:.2f}亿 chg30={None if chg is None else round(chg, 2)} "
        f"n={n_kept} → {OUT}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
