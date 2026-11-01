"""Binance 在 Tron 上的 USDT 持仓（DefiLlama binance-cex）。

口径：api.llama.fi/protocol/binance-cex → chainTvls.Tron.tokensInUsd 的 USDT。
不是 CryptoQuant；是 DefiLlama 标注地址合计，可能与外电差一点。

用法：
  python download.py
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT = HERE / "output" / "json" / "binance_tron_usdt.json"
URL = "https://api.llama.fi/protocol/binance-cex"
UA = {"User-Agent": "dashboard-data/binance-tron-usdt"}


def get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def day_of(ts: int | float) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()


def main() -> int:
    payload = get_json(URL)
    tron = ((payload.get("chainTvls") or {}).get("Tron") or {})
    series_raw = tron.get("tokensInUsd") or []
    rows: list[dict[str, Any]] = []
    for row in series_raw:
        if not isinstance(row, dict):
            continue
        tokens = row.get("tokens") or {}
        usdt = tokens.get("USDT")
        if usdt is None or row.get("date") is None:
            continue
        rows.append({"day": day_of(row["date"]), "usdt_usd": float(usdt)})
    # 同一天多点取最后
    by_day: dict[str, float] = {}
    for row in rows:
        by_day[row["day"]] = row["usdt_usd"]
    days = [{"day": d, "usdt_usd": by_day[d]} for d in sorted(by_day)]
    if len(days) >= 2:
        for i, row in enumerate(days):
            if i == 0:
                row["dod_pct"] = None
                row["dod_usd"] = None
                continue
            prev = days[i - 1]["usdt_usd"]
            row["dod_usd"] = row["usdt_usd"] - prev
            row["dod_pct"] = ((row["usdt_usd"] / prev - 1.0) * 100.0) if prev else None
    else:
        for row in days:
            row["dod_pct"] = None
            row["dod_usd"] = None

    latest = days[-1] if days else None
    doc = {
        "name": "binance_tron_usdt",
        "source": "defillama",
        "source_url": "https://defillama.com/cex/binance-cex",
        "methodology": (
            "DefiLlama protocol/binance-cex · chainTvls.Tron.tokensInUsd.USDT；"
            "标注地址合计，不是 CryptoQuant 交易所持仓口径。"
        ),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest": latest,
        "series": days,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if latest:
        print(
            f"wrote {OUT} day={latest['day']} usdt={latest['usdt_usd']:.0f} "
            f"dod={latest.get('dod_pct')}"
        )
    else:
        print(f"wrote {OUT} empty")
    return 0 if latest else 1


if __name__ == "__main__":
    raise SystemExit(main())
