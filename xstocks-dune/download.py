#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""xStocks 官方面板关键指标（只读 Dune 缓存，不 POST /execute）。

查询（dune.com/xstocks/xstocks）：
- 5996674 Total AUM
- 5393690 DEX volume by asset（含累积成交、日合计、CRCLx）
- 5382068 Unique Holders

不占执行额度；缓存过旧（>72h）仍落盘但标记 stale。
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "output" / "json" / "xstocks_dune.json"
Q_AUM = 5996674
Q_VOL = 5393690
Q_HOLDERS = 5382068
MAX_CACHE_AGE_H = 72.0
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
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_ended(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    if "." in text:
        head, _, tail = text.partition(".")
        frac, sign, offset = tail.partition("+")
        text = f"{head}.{frac[:6]}{sign}{offset}" if sign else f"{head}.{frac[:6]}"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fetch_rows(query_id: int, key: str, stop_after_days: int | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """分页拉缓存。若 stop_after_days，凑齐这么多完整日就停（省流量）。"""
    url = f"https://api.dune.com/api/v1/query/{query_id}/results?limit=1000"
    rows: list[dict[str, Any]] = []
    ended: str | None = None
    days_seen: set[str] = set()
    while url:
        payload = get_json(url, key)
        chunk = list((payload.get("result") or {}).get("rows") or [])
        rows.extend(chunk)
        ended = payload.get("execution_ended_at") or ended
        if stop_after_days:
            for row in chunk:
                day = str(row.get("day") or "")[:10]
                if len(day) == 10:
                    days_seen.add(day)
            if len(days_seen) >= stop_after_days and len(rows) >= 50:
                # 再确认最新日行数够（同日多标的）
                latest = max(days_seen)
                n_latest = sum(1 for r in rows if str(r.get("day") or "")[:10] == latest)
                if n_latest >= 20:
                    break
        url = payload.get("next_uri")
        if len(rows) > 40000:
            break
    return rows, ended


def age_hours(ended: str | None) -> float | None:
    dt = parse_ended(ended)
    if not dt:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def main() -> int:
    key = api_key()

    aum_rows, aum_ended = fetch_rows(Q_AUM, key)
    aum = float(aum_rows[0]["total_aum"]) if aum_rows else None

    hold_rows, hold_ended = fetch_rows(Q_HOLDERS, key)
    holders = None
    for row in hold_rows:
        if str(row.get("symbol") or "") == "All xStock":
            holders = int(row.get("unique_holders") or 0)
            break

    vol_rows, vol_ended = fetch_rows(Q_VOL, key, stop_after_days=4)
    by_day: dict[str, float] = {}
    crcl_by_day: dict[str, float] = {}
    cumul_by_day: dict[str, float] = {}
    for row in vol_rows:
        day = str(row.get("day") or "")[:10]
        if len(day) != 10:
            continue
        total = row.get("daily_total_volume_usd")
        if isinstance(total, (int, float)):
            by_day[day] = float(total)
        cum = row.get("cumulative_volume_usd")
        if isinstance(cum, (int, float)):
            # 同日各标的共享同一累积值，取最大防脏值
            prev_c = cumul_by_day.get(day)
            cumul_by_day[day] = float(cum) if prev_c is None else max(prev_c, float(cum))
        if str(row.get("symbol") or "").upper() == "CRCLX":
            try:
                crcl_by_day[day] = float(row.get("volume_usd") or 0)
            except (TypeError, ValueError):
                pass

    days = sorted(by_day)
    if not days:
        raise SystemExit("xstocks volume 缓存无日期")
    latest = days[-1]
    prev = days[-2] if len(days) >= 2 else None
    daily = by_day[latest]
    cumul = cumul_by_day.get(latest)
    prev_daily = by_day.get(prev) if prev else None
    dod = None
    if prev_daily and prev_daily > 0:
        dod = round((daily / prev_daily - 1.0) * 100.0, 2)

    ended_candidates = [aum_ended, vol_ended, hold_ended]
    ages = [age_hours(x) for x in ended_candidates]
    ages_ok = [a for a in ages if a is not None]
    max_age = max(ages_ok) if ages_ok else None
    if max_age is not None:
        stale = max_age > MAX_CACHE_AGE_H
    else:
        try:
            lag = (datetime.now(timezone.utc).date() - datetime.strptime(latest, "%Y-%m-%d").date()).days
        except ValueError:
            lag = 99
        stale = lag > 3

    doc = {
        "source": "dune.com/xstocks/xstocks (cache only, no execute)",
        "queries": {"aum": Q_AUM, "volume": Q_VOL, "holders": Q_HOLDERS},
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stale": stale,
        "max_cache_age_hours": max_age,
        "execution_ended_at": {
            "aum": aum_ended,
            "volume": vol_ended,
            "holders": hold_ended,
        },
        "day": latest,
        "prev_day": prev,
        "aum_usd": aum,
        "holders": holders,
        "dex_cumulative_usd": cumul,
        "dex_daily_usd": daily,
        "dex_daily_dod_pct": dod,
        "crclx_daily_usd": crcl_by_day.get(latest),
        "series_daily": [{"day": d, "usd": by_day[d]} for d in days[-60:]],
        "note": "累积成交为面板 DEX volume 口径，不是 CEX+DEX+铸造合计；持有者为 All xStock。",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[xstocks-dune] day={latest} aum={aum} cumul={cumul} daily={daily} "
        f"crclx={doc['crclx_daily_usd']} holders={holders} stale={stale} age={max_age}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
