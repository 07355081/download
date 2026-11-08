# -*- coding: utf-8 -*-
"""HyperEVM / Hyperliquid L1 日费用（DefiLlama dailyFees，免费）。

对应看板 Hyperliquid 页的 HyperEVM 链上费用口径；本文件用 Llama
summary/fees/hyperliquid-l1，不占 Dune。

输出：
  output/json/hyperevm_fees.json

用法：
  python download.py
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "output" / "json" / "hyperevm_fees.json"
API = "https://api.llama.fi/summary/fees/hyperliquid-l1?dataType=dailyFees"
UA = "dashboard-data/hyperevm-fees"
TIMEOUT = 90
ATTEMPTS = 3


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_json(url: str) -> Any:
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < ATTEMPTS:
                time.sleep(min(2**attempt, 12))
    raise RuntimeError(f"GET {url} failed: {last}")


def main() -> int:
    doc = http_json(API)
    rows: list[dict[str, Any]] = []
    for pt in doc.get("totalDataChart") or []:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        try:
            day = datetime.fromtimestamp(int(pt[0]), tz=timezone.utc).strftime("%Y-%m-%d")
            val = float(pt[1] or 0)
        except (TypeError, ValueError, OSError):
            continue
        if val < 0:
            continue
        rows.append({"day": day, "fees_usd": round(val, 2), "value": round(val, 2)})
    rows.sort(key=lambda r: r["day"])
    if len(rows) < 2:
        raise SystemExit("日序列不足")

    # 丢掉仍在汇总中的当天（Llama 常含不完整日）
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if rows[-1]["day"] >= today:
        rows = rows[:-1]
    if len(rows) < 2:
        raise SystemExit("完整日不足")

    latest = rows[-1]
    payload = {
        "code": "0",
        "msg": "success",
        "name": "hyperevm_fees",
        "description": "DefiLlama Hyperliquid L1 dailyFees（对应 HyperEVM 链上费用）",
        "source": "DefiLlama summary/fees/hyperliquid-l1 dailyFees",
        "source_url": "https://defillama.com/fees/chain/Hyperliquid",
        "updated_at": now_iso(),
        "latest_day": latest["day"],
        "latest_fees_usd": latest["fees_usd"],
        "row_count": len(rows),
        "data": rows,
        "series": [
            {
                "label": "HyperEVM 链上费用",
                "labelEn": "HyperEVM on-chain fees",
                "months": [{"month": r["day"], "usd": r["fees_usd"]} for r in rows],
            }
        ],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(f"wrote {OUT_PATH} rows={len(rows)} latest={latest['day']} fees={latest['fees_usd']:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
