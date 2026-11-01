#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Perp DEX 月成交与 Hyperliquid 份额 → Lark 新闻体。

全行业日成交：PerpFinder /api/data/perps-chart（其页面写明上游为 DefiLlama）。
Hyperliquid：CoinGecko 交易所成交量（BTC）乘当日比特币价格，换成美元。
DefiLlama derivatives 接口为 402，不走 Pro，不占 Dune。

只发最近一个已结束自然月，且月结后 10 日内：
成交创本序列完整月新低，或 Hyperliquid 份额创完整月新高。
不发日常，不回扫更早的月。
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
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_PATH = HERE / "output" / "json" / "perp_dex_share.json"
STATE_PATH = HERE / "output" / "perp_dex_share_lark.json"
LOG_PATH = HERE / "flash.log"
JSON_URL = "https://data.wublock123.com/dashboard/hyperliquid"
PAGE = "https://data.wublock123.com/dashboard/hyperliquid"
PERP_URL = "https://perpfinder.com/api/data/perps-chart?days=400"
HL_URL = "https://api.coingecko.com/api/v3/exchanges/hyperliquid/volume_chart?days=365"
BTC_URL = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=365&interval=daily"
TZ_CN = timezone(timedelta(hours=8))
FRESH_DAYS = 10
MIN_HL_DAYS = 20
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

sys.path.insert(0, str(ROOT))
from lark_copy import as_news  # noqa: E402


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
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def gen_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": as_news(text)}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = gen_sign(secret, ts)
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
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def http_json(url: str) -> Any:
    last: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "dashboard-data/perp-dex-share", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 2:
                raise
            last = exc
            time.sleep(2 + attempt)
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            if attempt == 2:
                raise
            time.sleep(1.5)
    raise RuntimeError(last)


def day_of(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def fetch_fresh() -> dict[str, dict[str, float]]:
    perp = http_json(PERP_URL)
    points = perp.get("chart") if isinstance(perp, dict) else None
    if not isinstance(points, list) or len(points) < 60:
        raise RuntimeError("PerpFinder 日成交不足")
    rows: dict[str, dict[str, float]] = {}
    for item in points:
        if not isinstance(item, dict):
            continue
        day = str(item.get("date") or "")
        if len(day) != 10:
            continue
        rows[day] = {"perp_dex_usd": float(item["volume"])}
    hl = http_json(HL_URL)
    btc = http_json(BTC_URL)
    prices: dict[str, float] = {}
    for ts, price in (btc.get("prices") or []):
        prices[day_of(int(ts))] = float(price)
    for ts, vol_btc in hl:
        day = day_of(int(ts))
        px = prices.get(day)
        if px is None or day not in rows:
            continue
        rows[day]["hl_usd"] = float(vol_btc) * px
        rows[day]["btc_usd"] = px
    if len(rows) < 60:
        raise RuntimeError("对齐后的交易日不足")
    return rows


def load_saved_days() -> dict[str, dict[str, float]]:
    if not OUT_PATH.is_file():
        return {}
    try:
        doc = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, dict[str, float]] = {}
    for item in doc.get("days") or []:
        if not isinstance(item, dict):
            continue
        day = str(item.get("day") or "")
        perp = item.get("perp_dex_usd")
        if len(day) != 10 or not isinstance(perp, (int, float)):
            continue
        row = {"perp_dex_usd": float(perp)}
        if isinstance(item.get("hl_usd"), (int, float)):
            row["hl_usd"] = float(item["hl_usd"])
        out[day] = row
    return out


def month_rows(days: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    perp_sum: dict[str, float] = defaultdict(float)
    hl_sum: dict[str, float] = defaultdict(float)
    perp_n: dict[str, int] = defaultdict(int)
    hl_n: dict[str, int] = defaultdict(int)
    for day, row in days.items():
        ym = day[:7]
        perp_sum[ym] += row["perp_dex_usd"]
        perp_n[ym] += 1
        if "hl_usd" in row:
            hl_sum[ym] += row["hl_usd"]
            hl_n[ym] += 1
    today = datetime.now(timezone.utc).date()
    rows = []
    for ym in sorted(perp_sum):
        year, month = int(ym[:4]), int(ym[5:7])
        need = monthrange(year, month)[1]
        complete = perp_n[ym] >= need - 1 and date(year, month, need) < today
        share = None
        if hl_n[ym] >= MIN_HL_DAYS and perp_sum[ym] > 0:
            share = hl_sum[ym] / perp_sum[ym] * 100
        rows.append({
            "month": ym,
            "complete": complete,
            "perp_dex_usd": round(perp_sum[ym], 2),
            "hl_usd": round(hl_sum[ym], 2) if hl_n[ym] else None,
            "hl_days": hl_n[ym],
            "perp_days": perp_n[ym],
            "hl_share_pct": None if share is None else round(share, 2),
        })
    return rows


def save_doc(days: dict[str, dict[str, float]], months: list[dict[str, Any]]) -> None:
    payload = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "PerpFinder perps-chart（DefiLlama 汇总）+ CoinGecko Hyperliquid 成交量×BTC 价格",
        "note": "只在最近完整月创成交新低或 Hyperliquid 份额新高、且月结 10 日内发新闻。非 DefiLlama Pro。",
        "months": months,
        "days": [
            {
                "day": day,
                "perp_dex_usd": round(row["perp_dex_usd"], 2),
                **({"hl_usd": round(row["hl_usd"], 2)} if "hl_usd" in row else {}),
            }
            for day, row in sorted(days.items())
        ],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)


def yi(usd: float) -> str:
    return f"{usd / 1e8:.0f} 亿美元"


def build_news(latest: dict[str, Any], peak: dict[str, Any], vol_low: bool, share_high: bool) -> str:
    drop = (peak["perp_dex_usd"] - latest["perp_dex_usd"]) / peak["perp_dex_usd"] * 100
    bits = [f"据吴说数据中心统计，{latest['month']} Perp DEX 成交约 {yi(latest['perp_dex_usd'])}"]
    if vol_low:
        bits[0] += "，为本序列完整月新低"
    bits[0] += "。"
    bits.append(
        f"峰值在 {peak['month']}，约 {yi(peak['perp_dex_usd'])}，本月较高点下降约 {drop:.1f}%。"
    )
    if latest.get("hl_share_pct") is not None:
        share = (
            f"同月 Hyperliquid 成交约 {yi(latest['hl_usd'])}，占 Perp DEX 约 {latest['hl_share_pct']:.1f}%"
        )
        if share_high:
            share += "，为本序列完整月新高"
        bits.append(share + "。")
    return "".join(bits) + f"\n{PAGE}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        fresh = fetch_fresh()
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError, ValueError, KeyError) as exc:
        print(f"[ERROR] 拉数失败: {exc}", file=sys.stderr)
        return 1
    days = load_saved_days()
    days.update(fresh)
    months = month_rows(days)
    save_doc(days, months)
    complete = [row for row in months if row["complete"]]
    if len(complete) < 3:
        print("[skip] 完整月不足")
        return 0
    latest = complete[-1]
    prior = complete[:-1]
    peak = max(complete, key=lambda row: row["perp_dex_usd"])
    vol_low = latest["perp_dex_usd"] < min(row["perp_dex_usd"] for row in prior) - 1
    prior_shares = [row["hl_share_pct"] for row in prior if isinstance(row.get("hl_share_pct"), (int, float))]
    share_high = (
        isinstance(latest.get("hl_share_pct"), (int, float))
        and bool(prior_shares)
        and latest["hl_share_pct"] > max(prior_shares) + 0.1
    )
    year, month = int(latest["month"][:4]), int(latest["month"][5:7])
    ended = date(year, month, monthrange(year, month)[1])
    age = (datetime.now(timezone.utc).date() - ended).days
    print(
        f"[perp-dex-share] month={latest['month']} perp={latest['perp_dex_usd']/1e8:.0f}亿 "
        f"hl_share={latest.get('hl_share_pct')} peak={peak['month']} {peak['perp_dex_usd']/1e8:.0f}亿 "
        f"vol_low={vol_low} share_high={share_high} age={age}d"
    )
    if age > FRESH_DAYS and not args.force:
        print("[skip] 最近完整月已超过 10 天")
        return 0
    if not vol_low and not share_high and not args.force:
        print("[skip] 未创成交新低，份额也未创新高")
        return 0
    text = build_news(latest, peak, vol_low, share_high)
    msg_id = f"perp-dex-share-{latest['month']}"
    print("---")
    print(f"id={msg_id}")
    print(as_news(text))
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0
    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    state = load_state()
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
        return 0
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    try:
        send_lark(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Lark 失败: {exc}", file=sys.stderr)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"=== {stamp} {msg_id} ok=False {exc}\n{text}\n\n")
        return 1
    print(f"[pushed] {msg_id}")
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-200:]
    save_state(state)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} {msg_id} ok=True\n{text}\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
