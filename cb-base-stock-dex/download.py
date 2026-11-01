# -*- coding: utf-8 -*-
"""Coinbase 在 Base 上发行的股票代币，按 DEX 池子加总近 24 小时成交。

名单来自 Coinbase 公开接口。成交来自 GeckoTerminal 各池 volume_usd.h24，
按池子地址去重。同一天重复跑会覆盖当天这一行。
这不是 Token Terminal 的 30 天滚动合计，也不是链上逐笔去重后的成交。
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
OUT = HERE / "output" / "json" / "daily.json"
LIST_URL = "https://api.coinbase.com/v1/tokenized-stocks"
POOLS = "https://api.geckoterminal.com/api/v2/networks/base/tokens/{addr}/pools?page={page}"
UA = "wublock-cb-base-stock-dex/1.0"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_json(url: str) -> Any:
    last: Exception | None = None
    for attempt in range(5):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 429:
                time.sleep(8 + attempt * 4)
                continue
            if exc.code not in (500, 502, 503):
                raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET failed {url}: {last}")


def venue_of(dex_id: str) -> str:
    text = dex_id.lower()
    if "aerodrome" in text:
        return "Aerodrome"
    if "uniswap-v4" in text or "uniswap_v4" in text:
        return "Uniswap v4"
    if "uniswap" in text:
        return "Uniswap 其他版本"
    if not text:
        return "其他"
    return dex_id


def num(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def pools_for(addr: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in range(1, 6):
        doc = get_json(POOLS.format(addr=addr, page=page))
        data = doc.get("data") if isinstance(doc, dict) else None
        if not isinstance(data, list) or not data:
            break
        rows.extend(item for item in data if isinstance(item, dict))
        if len(data) < 20:
            break
        time.sleep(2.1)
    return rows


def main() -> None:
    listed = get_json(LIST_URL)
    tokens = listed.get("tokens") if isinstance(listed, dict) else None
    if not isinstance(tokens, list) or not tokens:
        raise SystemExit("Coinbase 股票代币名单为空")
    seen: set[str] = set()
    by_dex: dict[str, float] = {}
    by_symbol: dict[str, float] = {}
    for index, token in enumerate(tokens, 1):
        if not isinstance(token, dict):
            continue
        addr = str(token.get("contract_address") or "").lower()
        symbol = str(token.get("symbol") or addr[:8])
        if not addr.startswith("0x"):
            continue
        try:
            pools = pools_for(addr)
        except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
            print(f"  skip {symbol}: {exc}", flush=True)
            time.sleep(2.1)
            continue
        for pool in pools:
            attrs = pool.get("attributes") if isinstance(pool.get("attributes"), dict) else {}
            pool_addr = str(attrs.get("address") or "").lower()
            if not pool_addr or pool_addr in seen:
                continue
            seen.add(pool_addr)
            volume = num((attrs.get("volume_usd") or {}).get("h24") if isinstance(attrs.get("volume_usd"), dict) else 0)
            if volume <= 0:
                continue
            rel = pool.get("relationships") if isinstance(pool.get("relationships"), dict) else {}
            dex = rel.get("dex") if isinstance(rel.get("dex"), dict) else {}
            dex_data = dex.get("data") if isinstance(dex.get("data"), dict) else {}
            venue = venue_of(str(dex_data.get("id") or ""))
            by_dex[venue] = by_dex.get(venue, 0.0) + volume
            by_symbol[symbol] = by_symbol.get(symbol, 0.0) + volume
        if index % 20 == 0:
            print(f"  tokens {index}/{len(tokens)} pools {len(seen)}", flush=True)
        time.sleep(2.1)

    total = sum(by_dex.values())
    if total <= 0:
        raise SystemExit("成交合计为 0，不覆盖旧文件")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    top = [
        {"symbol": symbol, "usd": round(usd, 2)}
        for symbol, usd in sorted(by_symbol.items(), key=lambda item: -item[1])[:5]
    ]
    row = {
        "day": day,
        "total": round(total, 2),
        "by_dex": {name: round(usd, 2) for name, usd in sorted(by_dex.items(), key=lambda item: -item[1])},
        "top": top,
        "pools": len(seen),
        "tokens": len(tokens),
    }
    old = {}
    if OUT.is_file():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            old = {}
    days = [item for item in old.get("days") or [] if isinstance(item, dict) and item.get("day") != day]
    days.append(row)
    days.sort(key=lambda item: str(item.get("day")))
    payload = {
        "updated_at": now_iso(),
        "source": "Coinbase tokenized-stocks + GeckoTerminal pool h24",
        "note": (
            "样本是 Coinbase 公开名单里的 Base 股票代币。"
            "成交是各池近 24 小时成交额，按池子地址去重后相加。"
            "同一 UTC 日重复抓取会覆盖当天。不是 Token Terminal 的 30 天逐笔合计。"
        ),
        "days": days[-400:],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(OUT)
    print(f"wrote {OUT} day={day} total={total:.0f} venues={len(by_dex)}", flush=True)


if __name__ == "__main__":
    main()
