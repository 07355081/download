#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""btc-index 派生指标:MVRV Z-Score 与 SSR(稳定币供应比)。

为什么需要这个脚本:
    glassnode 会员到期后, 001_mvrv-z-score 与 066_stablecoin-supply-ratio-ssr
    分别停在 2026-06-15 和 2026-03-21, 首页两个 tile 长期显示几个月前的数字。
    这两个指标用免费源可以重建, 因此不再依赖 glassnode:

    MVRV Z-Score —— 取 bitcoin-data.com(BGeometrics) 的成品值。
        本来想用 CoinMetrics 自算, 但 CapRealUSD(已实现市值)属付费指标;
        改用 市值/MVRV 反推已实现市值算出来的 Z 与主流口径差约 11%,
        而且 2010 年那几天样本太少会让标准差趋零、Z 值冲到 20 以上。
        BGeometrics 直接给成品, 口径与 Glassnode 一致, 只是历史从 2022-08 起,
        涵盖 2022 熊底到本轮牛市, 做历史分位够用。

    SSR —— BTC 市值(CoinMetrics community 免费档) / 稳定币总市值(本地 stablecoin 管线)。

输出沿用 Coinglass 的信封格式写进 btc-index/output/json/coinglass/,
文件名与前端 catalog 的 slug 对齐;前端在 btc-index-catalog.ts 里把
glassnode/001_mvrv-z-score 与 glassnode/066_stablecoin-supply-ratio-ssr
别名到这两个文件, 首页、报告页、估值面板会一起切过来。

用法:
    python btc-index/download_derived.py [--only mvrv-z|ssr] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
OUT_DIR = SCRIPT_DIR / "output" / "json" / "coinglass"
STABLECOIN_JSON = REPO_ROOT / "stablecoin" / "output" / "json" / "stablecoins_marketcap.json"

BGEO_MVRVZ_URL = "https://bitcoin-data.com/v1/mvrv-zscore"
COINMETRICS_URL = (
    "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
    "?assets=btc&metrics=CapMrktCurUSD&frequency=1d&page_size=10000"
)
USER_AGENT = "btc-index-derived/1.0"
HTTP_TIMEOUT = 60

# 产出健全性下限。低于这个点数说明上游给的是残缺响应, 不覆盖既有文件。
MIN_POINTS = 300


def http_get_json(url: str, retries: int = 3) -> Any:
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
            last_err = e
            if attempt < retries:
                continue
    raise RuntimeError(f"GET {url} failed after {retries} attempts: {last_err}")


def day_to_ms(day: str) -> int | None:
    try:
        dt = datetime.strptime(day[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return int(dt.timestamp() * 1000)


def write_envelope(path: Path, source: str, points: list[tuple[str, float]]) -> None:
    """按 Coinglass 信封格式落盘(前端 parseCoinglassEnvelope 直接吃这个结构)。"""
    points = sorted(points, key=lambda p: p[0])
    time_list: list[int] = []
    data_list: list[dict[str, float]] = []
    for day, value in points:
        ms = day_to_ms(day)
        if ms is None:
            continue
        time_list.append(ms)
        data_list.append({"value": value})

    payload = {
        "code": "0",
        "msg": "success",
        # 保留真实来源:文件放在 coinglass/ 目录只是沿用前端既有的目录约定。
        "source": source,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data": {"time_list": time_list, "data_list": data_list},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    print(f"  wrote {path.name}  points={len(data_list)}  last={points[-1][0]}={points[-1][1]:.4f}")


def build_mvrv_z() -> list[tuple[str, float]]:
    rows = http_get_json(BGEO_MVRVZ_URL)
    if not isinstance(rows, list):
        raise RuntimeError("bitcoin-data.com returned a non-list payload")
    out: list[tuple[str, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = str(row.get("d") or "")[:10]
        raw = row.get("mvrvZscore")
        if not day or raw is None:
            continue
        try:
            out.append((day, float(raw)))
        except (TypeError, ValueError):
            continue
    return out


def load_stablecoin_totals() -> dict[str, float]:
    if not STABLECOIN_JSON.is_file():
        raise RuntimeError(f"stablecoin json not found: {STABLECOIN_JSON}")
    payload = json.loads(STABLECOIN_JSON.read_text(encoding="utf-8"))
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise RuntimeError("stablecoin json has no data array")
    out: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = str(row.get("time") or "")[:10]
        total = row.get("all_stablecoins")
        if not day or total is None:
            continue
        try:
            total = float(total)
        except (TypeError, ValueError):
            continue
        if total > 0:
            out[day] = total
    return out


def build_ssr() -> list[tuple[str, float]]:
    payload = http_get_json(COINMETRICS_URL)
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("CoinMetrics returned no data array")

    market_cap: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = str(row.get("time") or "")[:10]
        raw = row.get("CapMrktCurUSD")
        if not day or raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            market_cap[day] = value

    stablecoins = load_stablecoin_totals()
    # SSR 只在两边都有数据的日子有定义;稳定币序列从 2017-11 起, 因此天然从那时开始。
    out = [
        (day, market_cap[day] / stablecoins[day])
        for day in sorted(market_cap.keys() & stablecoins.keys())
    ]
    return out


TARGETS = {
    "mvrv-z": ("mvrv-z-score.json", "bitcoin-data.com (BGeometrics)", build_mvrv_z),
    "ssr": (
        "stablecoin-supply-ratio-ssr.json",
        "CoinMetrics community (BTC market cap) / DefiLlama (stablecoin supply)",
        build_ssr,
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", choices=sorted(TARGETS), default=None)
    parser.add_argument("--dry-run", action="store_true", help="只取数与校验,不落盘")
    args = parser.parse_args()

    names = [args.only] if args.only else sorted(TARGETS)
    failed: list[str] = []

    for name in names:
        filename, source, builder = TARGETS[name]
        print(f"[{name}] {source}")
        try:
            points = builder()
        except (RuntimeError, OSError, ValueError) as e:
            print(f"  [ERROR] {e}")
            failed.append(name)
            continue
        if len(points) < MIN_POINTS:
            # 宁可留着旧文件也不要用残缺数据把好数据覆盖掉。
            print(f"  [ERROR] only {len(points)} points (< {MIN_POINTS}); refusing to overwrite")
            failed.append(name)
            continue
        if args.dry_run:
            print(f"  [dry-run] {len(points)} points, last={points[-1]}")
            continue
        write_envelope(OUT_DIR / filename, source, points)

    if failed:
        raise SystemExit(f"[FATAL] failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
