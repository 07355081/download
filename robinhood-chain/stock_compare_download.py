"""Robinhood 股票代币 vs Coinbase Base 股票代币：只读 Dune 缓存，不执行查询。

- Robinhood：公开查询 8071940 的「stock trading (spot)」（DEX + 场内）
- Coinbase/Base：公开查询 8386348（Coinbase tokenized stocks，全 DEX）
这不是 Token Terminal。两边窗口和计价都不保证与外电一致。
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "output" / "json" / "stock_token_compare.json"
RH_QUERY = 8071940
BASE_QUERY = 8386348
WINDOW = 30
ENV_FILES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]


def api_key() -> str:
    for path in ENV_FILES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("DUNE_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("未配置 DUNE_API_KEY")


def get_json(url: str, key: str) -> Any:
    req = urllib.request.Request(url, headers={"X-Dune-API-Key": key})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def all_rows(query_id: int, key: str) -> tuple[list[dict[str, Any]], str | None]:
    payload = get_json(
        f"https://api.dune.com/api/v1/query/{query_id}/results?limit=1000",
        key,
    )
    ended = payload.get("execution_ended_at")
    rows = list(payload.get("result", {}).get("rows") or [])
    while payload.get("next_uri"):
        payload = get_json(payload["next_uri"], key)
        rows.extend(payload.get("result", {}).get("rows") or [])
        ended = payload.get("execution_ended_at") or ended
    return rows, ended


def stock_series(rh_days: dict[str, float], base_days: dict[str, float], start: str, end: str) -> list[dict[str, Any]]:
    days = sorted(day for day in set(rh_days) | set(base_days) if start <= day <= end)
    out = []
    for label, source in (("Robinhood", rh_days), ("Coinbase Base", base_days)):
        points = [{"month": day, "usd": round(source[day], 2)} for day in days if day in source]
        if len(points) >= 2:
            out.append({"label": label, "labelEn": label, "months": points})
    return out


def day_of(value: Any) -> str:
    return str(value)[:10]


def sum_window(by_day: dict[str, float], end: str) -> float:
    start = (datetime.fromisoformat(end).date() - timedelta(days=WINDOW - 1)).isoformat()
    return sum(vol for day, vol in by_day.items() if start <= day <= end)


def main() -> int:
    key = api_key()
    rh_rows, rh_ended = all_rows(RH_QUERY, key)
    base_rows, base_ended = all_rows(BASE_QUERY, key)
    rh_days: dict[str, float] = {}
    for row in rh_rows:
        if row.get("series") != "stock trading (spot)":
            continue
        day = day_of(row.get("block_date"))
        rh_days[day] = rh_days.get(day, 0.0) + float(row.get("volume_usd") or 0)
    base_days: dict[str, float] = {}
    for row in base_rows:
        day = day_of(row.get("day"))
        base_days[day] = base_days.get(day, 0.0) + float(row.get("volume_usd") or 0)
    if not rh_days or not base_days:
        raise SystemExit("Dune 缓存缺股票代币成交")
    end = min(max(rh_days), max(base_days))
    start = (datetime.fromisoformat(end).date() - timedelta(days=WINDOW - 1)).isoformat()
    doc = {
        "name": "stock_token_compare",
        "source": "dune_cache",
        "methodology": (
            "只读 Dune 缓存，不新执行查询。"
            "Robinhood 为查询 8071940 的 stock trading (spot)；"
            "Coinbase 为查询 8386348 的 Base 股票代币全 DEX。"
            "不是 Token Terminal。"
        ),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_start": start,
        "window_end": end,
        "robinhood": {
            "query_id": RH_QUERY,
            "execution_ended_at": rh_ended,
            "volume_30d_usd": sum_window(rh_days, end),
            "latest_day": max(rh_days),
            "latest_day_usd": rh_days[max(rh_days)],
        },
        "series": stock_series(rh_days, base_days, start, end),
        "coinbase_base": {
            "query_id": BASE_QUERY,
            "execution_ended_at": base_ended,
            "volume_30d_usd": sum_window(base_days, end),
            "latest_day": max(base_days),
            "latest_day_usd": base_days[max(base_days)],
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    OUT.write_text(text, encoding="utf-8")
    (OUT.parent / "stock_compare.json").write_text(text, encoding="utf-8")
    print(
        f"wrote {OUT} window={start}..{end} "
        f"rh30={doc['robinhood']['volume_30d_usd']:.0f} "
        f"base30={doc['coinbase_base']['volume_30d_usd']:.0f} "
        f"rh_day={doc['robinhood']['latest_day_usd']:.0f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
