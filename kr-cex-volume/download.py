# -*- coding: utf-8 -*-
"""Upbit / Bithumb 近 24 小时成交（美元）日采样。

CoinGecko /exchanges/{id} 只有 trade_volume_24h_btc，没有美元涨幅。
每天存一笔：24 小时 BTC 成交量 × 当时比特币价格。涨幅用上一采样日自算。
不是交易所日 K，也补不回已经过去的网页涨幅。

输出：output/json/kr_cex_volume.json

用法：
  python download.py
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_PATH = HERE / "output" / "json" / "kr_cex_volume.json"
ENV_PATH = ROOT / ".env"

GECKO = "https://api.coingecko.com/api/v3"
UA = "dashboard-data/kr-cex-volume"
TIMEOUT = 45
ATTEMPTS = 4
EXCHANGES = ("upbit", "bithumb")
TZ_CN = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def shanghai_day() -> str:
    return datetime.now(TZ_CN).strftime("%Y-%m-%d")


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        text = line.strip().lstrip("\ufeff")
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def gecko_key() -> str:
    return (
        os.environ.get("COINGECKO_DEMO_API_KEY")
        or os.environ.get("COINGECKO_KEY")
        or os.environ.get("COINGECKO_API_KEY")
        or ""
    ).strip()


def http_get_json(url: str) -> Any:
    headers = {"User-Agent": UA, "Accept": "application/json"}
    key = gecko_key()
    if key:
        headers["x-cg-demo-api-key"] = key
    last: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = exc
            if attempt < ATTEMPTS and exc.code in (429, 500, 502, 503, 504):
                time.sleep(min(2**attempt, 20))
                continue
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < ATTEMPTS:
                time.sleep(min(2**attempt, 12))
                continue
            raise
    raise RuntimeError(f"GET {url} failed: {last}")


def volume_btc(exchange_id: str) -> float:
    doc = http_get_json(f"{GECKO}/exchanges/{exchange_id}")
    if not isinstance(doc, dict):
        raise RuntimeError(f"{exchange_id} 返回非对象")
    raw = doc.get("trade_volume_24h_btc")
    if not isinstance(raw, (int, float)) or raw <= 0:
        raise RuntimeError(f"{exchange_id} trade_volume_24h_btc 无效: {raw}")
    return float(raw)


def btc_price_usd() -> float:
    doc = http_get_json(f"{GECKO}/simple/price?ids=bitcoin&vs_currencies=usd")
    if not isinstance(doc, dict):
        raise RuntimeError("bitcoin 价格返回非对象")
    raw = (doc.get("bitcoin") or {}).get("usd")
    if not isinstance(raw, (int, float)) or raw <= 0:
        raise RuntimeError(f"bitcoin usd 无效: {raw}")
    return float(raw)


def load_rows() -> list[dict[str, Any]]:
    if not OUT_PATH.is_file():
        return []
    try:
        doc = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and row.get("time")]


def pct(curr: float, prev: float | None) -> float | None:
    if prev is None or prev == 0:
        return None
    return (curr - prev) / abs(prev) * 100.0


def main() -> None:
    load_env(ENV_PATH)
    day = shanghai_day()
    volumes = {name: volume_btc(name) for name in EXCHANGES}
    price = btc_price_usd()
    rows = [row for row in load_rows() if str(row.get("time")) != day]
    prev = rows[-1] if rows else None
    point: dict[str, Any] = {
        "time": day,
        "sampled_at": now_iso(),
        "btc_price_usd": price,
    }
    for name in EXCHANGES:
        usd = volumes[name] * price
        point[f"{name}_volume_btc"] = volumes[name]
        point[f"{name}_volume_usd"] = usd
        prev_usd = None
        if prev is not None and isinstance(prev.get(f"{name}_volume_usd"), (int, float)):
            prev_usd = float(prev[f"{name}_volume_usd"])
        point[f"{name}_dod_pct"] = pct(usd, prev_usd)
    rows.append(point)
    rows.sort(key=lambda row: str(row["time"]))
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "meta": {
            "source": "coingecko",
            "exchanges": list(EXCHANGES),
            "note": "trade_volume_24h_btc × 采样时比特币美元价。涨幅相对上一采样日，不是 CoinGecko 页面上的涨幅。",
            "updated_at": now_iso(),
        },
        "data": rows,
    }
    OUT_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[kr-cex-volume] {day} btc={price:.2f}")
    for name in EXCHANGES:
        dod = point[f"{name}_dod_pct"]
        dod_s = "n/a" if dod is None else f"{dod:+.1f}%"
        print(f"  {name} {point[f'{name}_volume_usd'] / 1e8:.2f} 亿美元  环比 {dod_s}")
    print(f"  → {OUT_PATH}  ({len(rows)} 天)")


if __name__ == "__main__":
    main()
