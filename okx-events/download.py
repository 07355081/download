#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OKX 事件合约系列快照（公开 API，免费）。

GET /api/v5/public/event-contract/series
可选抽查 UPDOWN-5MIN 是否有 live 市场。

输出：output/json/okx_events.json
不占 Dune。
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "okx_events.json"
BASE = "https://www.okx.com/api/v5"
UA = "wublock-okx-events/1.0"
TIMEOUT = 30
WATCH = ("BTC-UPDOWN-5MIN", "ETH-UPDOWN-5MIN", "SOL-UPDOWN-5MIN")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def http_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_data(path: str, params: str = "") -> list[dict[str, Any]]:
    url = f"{BASE}{path}"
    if params:
        url = f"{url}?{params}"
    doc = http_json(url)
    if str(doc.get("code")) != "0":
        raise RuntimeError(f"{path} code={doc.get('code')} msg={doc.get('msg')}")
    data = doc.get("data")
    if not isinstance(data, list):
        raise RuntimeError(f"{path} 无 data 列表")
    return [row for row in data if isinstance(row, dict)]


def label_of(row: dict[str, Any]) -> str:
    sid = str(row.get("seriesId") or "")
    sett = row.get("settlement") if isinstance(row.get("settlement"), dict) else {}
    underlying = str(sett.get("underlying") or "").replace("-USDT", "")
    method = str(sett.get("method") or "")
    freq = str(row.get("freq") or "")
    title = str(row.get("title") or "").strip()
    if title:
        return title
    return f"{underlying} {method} {freq}".strip() or sid


def load_days() -> list[dict[str, Any]]:
    if not OUT_PATH.is_file():
        return []
    try:
        doc = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    data = doc.get("data") if isinstance(doc, dict) else None
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def main() -> int:
    print("[okx-events] fetch event-contract/series …")
    series = get_data("/public/event-contract/series")
    if not series:
        raise RuntimeError("系列为空")
    rows = []
    for item in series:
        sid = str(item.get("seriesId") or "")
        if not sid:
            continue
        sett = item.get("settlement") if isinstance(item.get("settlement"), dict) else {}
        rows.append(
            {
                "series_id": sid,
                "title": label_of(item),
                "category": str(item.get("category") or ""),
                "freq": str(item.get("freq") or ""),
                "method": str(sett.get("method") or ""),
                "underlying": str(sett.get("underlying") or ""),
            }
        )
    rows.sort(key=lambda r: r["series_id"])
    live_bits: dict[str, int] = {}
    for sid in WATCH:
        try:
            markets = get_data("/public/event-contract/markets", f"seriesId={sid}&state=live")
            live_bits[sid] = len(markets)
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] live {sid}: {exc}")
            live_bits[sid] = 0
    day = utc_day()
    fingerprint = "|".join(r["series_id"] for r in rows)
    snap = {
        "day": day,
        "series_count": len(rows),
        "updown_5min_live": {k: live_bits.get(k, 0) for k in WATCH},
        "fingerprint": fingerprint,
    }
    history = [row for row in load_days() if str(row.get("day")) != day]
    history.append(
        {
            "day": day,
            "series_count": len(rows),
            "btc_5min_live": live_bits.get("BTC-UPDOWN-5MIN", 0),
            "eth_5min_live": live_bits.get("ETH-UPDOWN-5MIN", 0),
            "sol_5min_live": live_bits.get("SOL-UPDOWN-5MIN", 0),
        }
    )
    history.sort(key=lambda r: str(r.get("day") or ""))
    history = history[-120:]
    payload = {
        "updated_at": now_iso(),
        "source": "okx",
        "source_url": "https://www.okx.com/docs-v5/en/#public-data-rest-api-get-event-contract-series",
        "note": "OKX 公开事件合约系列。不是成交额排行；地区可用性以交易所前端为准。",
        "latest": {
            **snap,
            "series": rows,
            "highlights": [r for r in rows if "UPDOWN-5MIN" in r["series_id"] or "UPDOWN-15MIN" in r["series_id"]],
        },
        "data": history,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    live_txt = ",".join(f"{k.split('-')[0]}={v}" for k, v in live_bits.items())
    print(f"[okx-events] day={day} series={len(rows)} live[{live_txt}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
