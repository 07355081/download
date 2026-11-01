#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hyperliquid 巨鲸名义多空合计（CoinGlass whale-position，免费额度内）。

口径：名义 >100 万美元的持仓按标记价加总；净多头 = 多头名义 − 空头名义。
不是 Glassnode 分群序列。

输出：output/json/whale_net_long.json
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_PATH = HERE / "output" / "json" / "whale_net_long.json"
URL = "https://open-api-v4.coinglass.com/api/hyperliquid/whale-position"
UA = "wublock-hl-whale-net/1.0"
ENV_FILES = [
    ROOT / ".env",
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in ENV_FILES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    if os.environ.get("COINGLASS_API_KEY"):
        env["COINGLASS_API_KEY"] = os.environ["COINGLASS_API_KEY"]
    if not env.get("COINGLASS_API_KEY"):
        common = ROOT / "coinglass-history" / "_common.py"
        if common.is_file():
            for line in common.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("COINGLASS_API_KEY"):
                    env["COINGLASS_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    return env


def fetch_rows(api_key: str) -> list[dict[str, Any]]:
    req = urllib.request.Request(
        URL,
        headers={"CG-API-KEY": api_key, "accept": "application/json", "User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if str(payload.get("code", "0")) not in ("0", "200"):
        raise RuntimeError(f"CoinGlass code={payload.get('code')} msg={payload.get('msg')}")
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("whale-position 无列表")
    return [row for row in data if isinstance(row, dict)]


def num(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    if out != out or out in (float("inf"), float("-inf")):
        return 0.0
    return out


def aggregate(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    long_usd = short_usd = 0.0
    n_long = n_short = 0
    for row in rows:
        size = num(row.get("position_size"))
        value = abs(num(row.get("position_value_usd")))
        if value <= 0 or size == 0:
            continue
        if size > 0:
            long_usd += value
            n_long += 1
        else:
            short_usd += value
            n_short += 1
    return {
        "long_usd": round(long_usd, 2),
        "short_usd": round(short_usd, 2),
        "net_usd": round(long_usd - short_usd, 2),
        "n_long": n_long,
        "n_short": n_short,
        "n": n_long + n_short,
    }


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
    env = load_env()
    api_key = (env.get("COINGLASS_API_KEY") or "").strip()
    if not api_key:
        raise SystemExit("缺少 COINGLASS_API_KEY")
    print("[whale-net-long] fetch CoinGlass hyperliquid/whale-position …")
    rows = fetch_rows(api_key)
    snap = aggregate(rows)
    day = utc_day()
    point = {"day": day, **snap}
    history = [row for row in load_days() if str(row.get("day")) != day]
    history.append(point)
    history.sort(key=lambda row: str(row.get("day") or ""))
    history = history[-400:]
    payload = {
        "updated_at": now_iso(),
        "source": "coinglass",
        "source_url": "https://open-api-v4.coinglass.com/api/hyperliquid/whale-position",
        "note": (
            "CoinGlass Hyperliquid 巨鲸持仓（名义约>100万美元）多空名义加总；"
            "净多头=多头名义−空头名义。不是 Glassnode 分群口径。"
        ),
        "latest": point,
        "data": history,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(
        f"[whale-net-long] day={day} n={snap['n']} "
        f"long={snap['long_usd']/1e8:.2f}亿 short={snap['short_usd']/1e8:.2f}亿 "
        f"net={snap['net_usd']/1e8:.2f}亿"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
