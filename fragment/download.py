# -*- coding: utf-8 -*-
"""Fragment 协议费用日序列（DefiLlama dailyFees，免费）。

对应 Messari 稿里的 Fragment 结算近邻，但口径是协议费用，不是 Telegram 产品结算。
不占 Dune。

输出 output/json/fragment_fees.json
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT = HERE / "output" / "json" / "fragment_fees.json"
API = "https://api.llama.fi/summary/fees/fragment?dataType=dailyFees"
UA = "dashboard-data/fragment"
SOURCE_URL = "https://defillama.com/protocol/fragment"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    doc = http_get_json(API)
    chart = doc.get("totalDataChart") if isinstance(doc, dict) else None
    if not isinstance(chart, list) or not chart:
        raise RuntimeError("fragment dailyFees 无 totalDataChart")

    rows: list[dict[str, Any]] = []
    for pt in chart:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        ts, val = pt[0], pt[1]
        if not isinstance(ts, (int, float)) or not isinstance(val, (int, float)):
            continue
        day = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        rows.append({"date": day, "fees_usd": float(val)})
    rows.sort(key=lambda item: str(item["date"]))
    latest = rows[-1]
    prev = rows[-2] if len(rows) >= 2 else None
    last7 = rows[-7:]
    payload = {
        "code": "0",
        "msg": "success",
        "source": "defillama",
        "source_url": SOURCE_URL,
        "metric": "dailyFees",
        "updated_at": now_iso(),
        "latest": latest,
        "prev": prev,
        "last7_usd": sum(float(item["fees_usd"]) for item in last7),
        "row_count": len(rows),
        "data": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(OUT)
    print(f"wrote {OUT} latest={latest['date']} fees={latest['fees_usd']:.0f} rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
