#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hyperliquid SpaceX 相关永续短时大跌 → Lark 新闻。

盯 HIP-3 里名称含 SPACEX / SPCX 的市场（如 vntl:SPACEX、xyz:SPCX）。
近 30 分钟 5 分钟 K 线最高到最低跌幅达到阈值才发一条新闻，可直接发稿。
不写爆仓人数、仓位数、保证金中位数（公开接口没有）。
不回扫历史上的闪崩。

用法：
  python monitor.py
  python monitor.py --push
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_PATH = HERE / "output" / "json" / "spacex_perp.json"
STATE_PATH = HERE / "output" / "lark_state.json"
LOG_PATH = HERE / "monitor.log"
JSON_URL = "https://data.wublock123.com/dashboard/tradfi"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/tradfi"
INFO_URL = "https://api.hyperliquid.xyz/info"
TZ_CN = timezone(timedelta(hours=8))
CRASH_PCT = float(os.environ.get("SPACEX_CRASH_PCT") or 20.0)
WINDOW_MS = 30 * 60 * 1000
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

sys.path.insert(0, str(ROOT))
from lark_copy import as_news  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET", "SPACEX_CRASH_PCT"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def gen_lark_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark_text(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = gen_lark_sign(secret, ts)
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    if body:
        result = json.loads(body)
        if str(result.get("code", "0")) != "0":
            raise RuntimeError(f"Lark 返回异常: {result}")


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": []}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": []}
    ids = doc.get("sentIds") if isinstance(doc, dict) else None
    return {"sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else []}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = now_iso()
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def append_log(stamp: str, msg_id: str, ok: bool, text: str) -> None:
    line = json.dumps({"ts": stamp, "id": msg_id, "ok": ok, "text": text}, ensure_ascii=False)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def info(body: dict[str, Any]) -> Any:
    data = json.dumps(body).encode("utf-8")
    last: Exception | None = None
    for attempt in range(4):
        req = urllib.request.Request(
            INFO_URL,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "dashboard-data/spacex-perp"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in (429, 500, 502, 503):
                raise
            time.sleep(1.2 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"Hyperliquid info 失败: {last}")


def num(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def list_spacex_markets() -> list[dict[str, Any]]:
    dexs = info({"type": "perpDexs"})
    out: list[dict[str, Any]] = []
    if not isinstance(dexs, list):
        return out
    for dex in dexs:
        if not isinstance(dex, dict) or not dex.get("name"):
            continue
        dex_name = str(dex["name"])
        payload = info({"type": "metaAndAssetCtxs", "dex": dex_name})
        if not isinstance(payload, list) or len(payload) < 2:
            continue
        meta, ctxs = payload[0], payload[1]
        universe = meta.get("universe") if isinstance(meta, dict) else None
        if not isinstance(universe, list) or not isinstance(ctxs, list):
            continue
        for asset, ctx in zip(universe, ctxs):
            if not isinstance(asset, dict) or not isinstance(ctx, dict):
                continue
            coin = str(asset.get("name") or "")
            low = coin.lower()
            if "spacex" not in low and "spcx" not in low:
                continue
            mark = num(ctx.get("markPx") or ctx.get("midPx"))
            oi = num(ctx.get("openInterest")) * mark
            out.append({
                "dex": dex_name,
                "coin": coin,
                "mark": mark,
                "day_ntl_vlm": num(ctx.get("dayNtlVlm")),
                "oi_usd": oi,
            })
        time.sleep(0.05)
    out.sort(key=lambda row: row["day_ntl_vlm"], reverse=True)
    return out


def candle_window(coin: str) -> list[dict[str, float]]:
    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = end - WINDOW_MS - 5 * 60 * 1000
    candles = info({
        "type": "candleSnapshot",
        "req": {"coin": coin, "interval": "5m", "startTime": start, "endTime": end},
    })
    rows: list[dict[str, float]] = []
    if not isinstance(candles, list):
        return rows
    cutoff = end - WINDOW_MS
    for candle in candles:
        if not isinstance(candle, dict):
            continue
        ts = int(candle.get("t") or 0)
        if ts < cutoff:
            continue
        high = num(candle.get("h"))
        low = num(candle.get("l"))
        close = num(candle.get("c"))
        if high <= 0 or low <= 0:
            continue
        rows.append({"t": float(ts), "h": high, "l": low, "c": close})
    rows.sort(key=lambda row: row["t"])
    return rows


def detect_crash(market: dict[str, Any]) -> dict[str, Any] | None:
    bars = candle_window(market["coin"])
    if len(bars) < 2:
        return None
    high = max(bar["h"] for bar in bars)
    low = min(bar["l"] for bar in bars)
    if high <= 0:
        return None
    drop = (high - low) / high * 100.0
    if drop < CRASH_PCT:
        return None
    return {
        **market,
        "window_high": round(high, 4),
        "window_low": round(low, 4),
        "drop_pct": round(drop, 2),
        "bars": len(bars),
        "asof": now_iso(),
    }


def yi(usd: float) -> str:
    if usd >= 1e8:
        return f"{usd / 1e8:.2f} 亿美元"
    if usd >= 1e4:
        return f"{usd / 1e4:.0f} 万美元"
    return f"{usd:.0f} 美元"


def build_news(row: dict[str, Any]) -> str:
    now = datetime.now(TZ_CN)
    stamp = f"{now.year} 年 {now.month} 月 {now.day} 日 {now.hour:02d}:{now.minute:02d}"
    text = (
        f"吴说获悉，据吴说数据中心统计，截至 {stamp}（UTC+8），"
        f"Hyperliquid 上 {row['coin']}（{row['dex']}）永续近约 30 分钟内"
        f"最高约 {row['window_high']:.2f} 美元、最低约 {row['window_low']:.2f} 美元，"
        f"高低点落差约 {row['drop_pct']:.1f}%。现报约 {row['mark']:.2f} 美元。"
        f"过去 24 小时名义成交约 {yi(float(row['day_ntl_vlm']))}，"
        f"未平仓约 {yi(float(row['oi_usd']))}。"
        f"这是未上市公司估值相关永续，不是真实股票；公开接口没有爆仓人数和保证金中位数。"
        f"\n{DASHBOARD_URL}"
    )
    return as_news(text)


def save_snapshot(markets: list[dict[str, Any]], crash: dict[str, Any] | None) -> None:
    payload = {
        "updated_at": now_iso(),
        "source": "hyperliquid info metaAndAssetCtxs + candleSnapshot",
        "crash_pct": CRASH_PCT,
        "window_minutes": 30,
        "note": "短时高低点落差达到阈值才发新闻；不写爆仓人数；不回扫历史闪崩。",
        "markets": markets,
        "latest_crash": crash,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    try:
        markets = list_spacex_markets()
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] 拉市场失败: {exc}", file=sys.stderr)
        return 1
    if not markets:
        print("[skip] 没有 SpaceX/SPCX 市场")
        save_snapshot([], None)
        return 0

    crash: dict[str, Any] | None = None
    for market in markets:
        try:
            found = detect_crash(market)
        except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[warn] candles {market['coin']}: {exc}")
            continue
        if found:
            crash = found
            break
        time.sleep(0.05)

    save_snapshot(markets, crash)
    for market in markets[:5]:
        print(
            f"  {market['coin']} mark={market['mark']} "
            f"vol24={market['day_ntl_vlm']:.0f} oi={market['oi_usd']:.0f}"
        )
    if not crash:
        print(f"[skip] 近 30 分钟高低点落差未到 {CRASH_PCT:.0f}%")
        return 0

    day = datetime.now(TZ_CN).strftime("%Y-%m-%d")
    coin_key = str(crash["coin"]).replace(":", "-").lower()
    msg_id = f"spacex-crash-{coin_key}-{day}"
    text = build_news(crash)
    state = load_state()
    print("---")
    print(f"id={msg_id} drop={crash['drop_pct']}% already={msg_id in state['sentIds']}")
    print(text)
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0

    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
        return 0
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    try:
        send_lark_text(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Lark 失败: {exc}", file=sys.stderr)
        append_log(stamp, msg_id, False, text)
        return 1
    print(f"[pushed] {msg_id}")
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-200:]
    save_state(state)
    append_log(stamp, msg_id, True, text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
