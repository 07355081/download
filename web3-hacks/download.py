"""Download DefiLlama Web3 hacks list and aggregate by calendar month (UTC).

Free public API, no Dune / CoinGlass.

Outputs:
  output/json/web3_hacks.json
    months: [{month, count, loss_usd, top_name, top_loss_usd, events:[...]}, ...]
    events: raw normalized event list (recent years kept in full file)

口径：DefiLlama Hacks（amount 美元）。与 GoPlus 社区「重大事件」次数/金额常不一致，写稿须注明。

Usage:
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
OUT_PATH = JSON_DIR / "web3_hacks.json"

API = "https://api.llama.fi/hacks"
UA = "dashboard-data/web3-hacks"
TIMEOUT = 90
ATTEMPTS = 3


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


def month_of_ts(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m")


def day_of_ts(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def normalize_event(row: dict[str, Any]) -> dict[str, Any] | None:
    ts = row.get("date")
    if not isinstance(ts, (int, float)):
        return None
    ts_i = int(ts)
    amount = row.get("amount")
    try:
        loss = float(amount) if amount is not None else 0.0
    except (TypeError, ValueError):
        loss = 0.0
    if loss < 0:
        loss = 0.0
    returned = row.get("returnedFunds")
    try:
        returned_f = float(returned) if returned is not None else None
    except (TypeError, ValueError):
        returned_f = None
    chains = row.get("chain")
    if isinstance(chains, list):
        chain_s = [str(c) for c in chains if c]
    elif chains:
        chain_s = [str(chains)]
    else:
        chain_s = []
    return {
        "day": day_of_ts(ts_i),
        "month": month_of_ts(ts_i),
        "ts": ts_i,
        "name": str(row.get("name") or "—"),
        "loss_usd": loss,
        "returned_usd": returned_f,
        "classification": str(row.get("classification") or "") or None,
        "technique": str(row.get("technique") or "") or None,
        "target_type": str(row.get("targetType") or "") or None,
        "bridge_hack": bool(row.get("bridgeHack")),
        "chain": chain_s,
        "source": str(row.get("source") or "") or None,
        "defillama_id": str(row.get("defillamaId") or "") or None,
    }


def build_months(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        buckets.setdefault(ev["month"], []).append(ev)

    # A calendar month is "complete" if today (UTC) is past that month's last day.
    today = datetime.now(timezone.utc).date()
    out: list[dict[str, Any]] = []
    for month in sorted(buckets):
        y, m = int(month[:4]), int(month[5:7])
        if m == 12:
            next_month_start = datetime(y + 1, 1, 1, tzinfo=timezone.utc).date()
        else:
            next_month_start = datetime(y, m + 1, 1, tzinfo=timezone.utc).date()
        complete = today >= next_month_start

        items = sorted(buckets[month], key=lambda e: (-e["loss_usd"], e["day"], e["name"]))
        loss = sum(e["loss_usd"] for e in items)
        top = items[0] if items else None
        out.append(
            {
                "month": month,
                "count": len(items),
                "loss_usd": loss,
                "top_name": top["name"] if top else None,
                "top_loss_usd": top["loss_usd"] if top else None,
                "top_day": top["day"] if top else None,
                "complete": complete,
                "events": items,
            }
        )
    return out


def main() -> None:
    print("[web3-hacks] fetch DefiLlama /hacks…")
    raw = http_get_json(API)
    if not isinstance(raw, list):
        raise RuntimeError("unexpected /hacks payload (not a list)")

    events: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        ev = normalize_event(row)
        if ev:
            events.append(ev)
    events.sort(key=lambda e: (e["day"], e["name"]))

    months = build_months(events)
    complete = [m for m in months if m["complete"]]
    latest_complete = complete[-1]["month"] if complete else None
    latest_any = months[-1]["month"] if months else None

    # Slim monthly series for charts / flash (drop per-month full event lists in months_summary)
    months_summary = [
        {
            "month": m["month"],
            "count": m["count"],
            "loss_usd": m["loss_usd"],
            "top_name": m["top_name"],
            "top_loss_usd": m["top_loss_usd"],
            "top_day": m["top_day"],
            "complete": m["complete"],
        }
        for m in months
    ]

    doc = {
        "code": "0",
        "msg": "success",
        "updated_at": now_iso(),
        "source": "defillama",
        "source_url": "https://defillama.com/hacks",
        "api": API,
        "description": "DefiLlama Web3 hacks 按 UTC 自然月汇总（次数与损失额）",
        "disclaimer": "口径为 DefiLlama Hacks，与 GoPlus 等社区「重大事件」统计常不一致。",
        "event_count": len(events),
        "latest_complete_month": latest_complete,
        "latest_month": latest_any,
        "months": months_summary,
        "series": [
            {
                "label": "月损失",
                "labelEn": "Monthly loss",
                "months": [
                    {"month": m["month"], "usd": m["loss_usd"]}
                    for m in months_summary
                    if isinstance(m.get("loss_usd"), (int, float))
                ],
            }
        ],
        # Keep last ~24 months of event detail for threshold / drill-down
        "events": [e for e in events if e["month"] >= (months_summary[-24]["month"] if len(months_summary) >= 24 else "1970-01")],
    }

    JSON_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(OUT_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(
        f"[web3-hacks] wrote {OUT_PATH} events={len(events)} months={len(months_summary)} "
        f"latest_complete={latest_complete}"
    )
    if latest_complete:
        row = next(m for m in months_summary if m["month"] == latest_complete)
        print(
            f"  {latest_complete}: count={row['count']} loss=${row['loss_usd']:,.0f} "
            f"top={row['top_name']} (${(row['top_loss_usd'] or 0):,.0f})"
        )


if __name__ == "__main__":
    main()
