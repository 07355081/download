#!/usr/bin/env python3
"""EDGE → Lark 新闻体，只推最新过线，不回扫历史。

过线任一即可：
- 近 1 小时相对高点回撤 ≤ -20%，或近 1 小时涨跌 ≤ -20%
- 24 小时 |涨跌| ≥ 20%
- 较近 7 日最高价累计跌幅 ≥ 50%
- Binance |资金费率|展示百分数 ≥ 50（若有）
- 全网 1h 爆仓 ≥ 100 万美元，或 24h 爆仓 ≥ 300 万美元，或 24h 排名进入前 4

同一自然日（上海）只发一条。

用法：
  python flash.py
  python flash.py --push
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
JSON_CANDIDATES = [
    HERE / "output" / "json" / "edge_spot.json",
    Path("/root/dashboard/public/json/edge-spot/edge_spot.json"),
]
STATE_PATH = HERE / "output" / "edge_spot_lark.json"
LOG_PATH = HERE / "flash.log"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/edge-spot"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
MOVE_PCT = 20.0
HOUR_DROP_PCT = 20.0
WEEK_DROP_PCT = 50.0
FUNDING_PCT = 50.0
LIQ_1H_USD = 1_000_000.0
LIQ_24H_USD = 3_000_000.0
LIQ_RANK = 4

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
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
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


def load_doc() -> dict[str, Any]:
    for path in JSON_CANDIDATES:
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("live"), dict):
                print(f"[edge-spot] json={path}")
                return doc
    raise FileNotFoundError("找不到 edge_spot.json，请先跑 download.py")


def pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def funding_s(value: float) -> str:
    sign = "+" if value > 0 else ""
    if abs(value) >= 10:
        return f"{sign}{value:.1f}%"
    return f"{sign}{value:.4f}%"


def price_s(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 1:
        return f"{value:.4f}"
    if value >= 0.1:
        return f"{value:.4f}"
    return f"{value:.6f}".rstrip("0").rstrip(".")


def usd_cn(value: float) -> str:
    if value >= 1e8:
        return f"{value / 1e8:.2f} 亿美元"
    if value >= 1e4:
        return f"{value / 1e4:.0f} 万美元"
    return f"{value:.0f} 美元"


def build_news(doc: dict[str, Any]) -> tuple[str, str] | None:
    live = doc["live"]
    chg = float(live.get("chg_24h_pct") or 0)
    drop_1h = live.get("drop_1h_from_high_pct")
    drop_1h_f = float(drop_1h) if isinstance(drop_1h, (int, float)) else None
    chg_1h = live.get("chg_1h_pct")
    chg_1h_f = float(chg_1h) if isinstance(chg_1h, (int, float)) else None
    week_drop = live.get("drop_from_week_high_pct")
    week_drop_f = float(week_drop) if isinstance(week_drop, (int, float)) else None
    funding = live.get("binance_funding_rate_pct")
    funding_f = float(funding) if isinstance(funding, (int, float)) else None
    liq24 = float(live.get("liquidation_usd_24h") or 0)
    liq1h = float(live.get("liquidation_usd_1h") or 0)
    rank = live.get("liquidation_rank_24h")
    rank_i = int(rank) if isinstance(rank, (int, float)) and rank else None

    reasons: list[str] = []
    if drop_1h_f is not None and drop_1h_f <= -HOUR_DROP_PCT:
        reasons.append(f"近 1 小时相对高点约 {pct(drop_1h_f)}")
    if chg_1h_f is not None and chg_1h_f <= -HOUR_DROP_PCT:
        reasons.append(f"近 1 小时约 {pct(chg_1h_f)}")
    if abs(chg) >= MOVE_PCT:
        reasons.append(f"过去 24 小时约 {pct(chg)}")
    if week_drop_f is not None and week_drop_f <= -WEEK_DROP_PCT:
        reasons.append(f"较近 7 日高点累计约 {pct(week_drop_f)}")
    if funding_f is not None and abs(funding_f) >= FUNDING_PCT:
        reasons.append(f"Binance 资金费率约 {funding_s(funding_f)}")
    if liq1h >= LIQ_1H_USD:
        reasons.append(f"全网近 1 小时爆仓约 {usd_cn(liq1h)}")
    if liq24 >= LIQ_24H_USD:
        reasons.append(f"全网近 24 小时爆仓约 {usd_cn(liq24)}")
    if rank_i is not None and rank_i <= LIQ_RANK:
        reasons.append(f"24 小时爆仓排名第 {rank_i}")
    if not reasons:
        return None

    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
    day = datetime.now(TZ_CN).strftime("%Y-%m-%d")
    high1 = live.get("high_1h")
    low1 = live.get("low_1h")
    hour_bit = ""
    if high1 and low1:
        hour_bit = f"近 1 小时高点约 {price_s(float(high1))} 美元、低点约 {price_s(float(low1))} 美元。"
    low24_bit = f"过去 24 小时最低约触及 {price_s(float(live['low_24h']))} 美元。" if live.get("low_24h") else ""
    liq_bit = ""
    if liq24 > 0 or liq1h > 0:
        long1 = float(live.get("long_liquidation_usd_1h") or 0)
        short1 = float(live.get("short_liquidation_usd_1h") or 0)
        long24 = float(live.get("long_liquidation_usd_24h") or 0)
        short24 = float(live.get("short_liquidation_usd_24h") or 0)
        parts = []
        if liq1h > 0:
            parts.append(
                f"近 1 小时全网爆仓约 {usd_cn(liq1h)}（多约 {usd_cn(long1)}、空约 {usd_cn(short1)}）"
            )
        if liq24 > 0:
            rank_s = f"，排名第 {rank_i}" if rank_i else ""
            parts.append(
                f"近 24 小时约 {usd_cn(liq24)}{rank_s}（多约 {usd_cn(long24)}、空约 {usd_cn(short24)}）"
            )
        liq_bit = "；".join(parts) + "。"
    funding_bit = ""
    if funding_f is not None:
        funding_bit = f"Binance EDGEUSDT 资金费率约 {funding_s(funding_f)}。"

    text = (
        f"据吴说数据中心统计，截至 {stamp}（UTC+8），EDGE 现报约 "
        f"{price_s(float(live['price']))} 美元（OKX 永续），{'，'.join(reasons)}。"
        f"{hour_bit}{low24_bit}{liq_bit}{funding_bit}"
        f"价格口径为 OKX 公开永续；爆仓为 CoinGlass。"
        f"\n{PAGE}"
    )
    return f"edge-cg-{day}", text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        doc = load_doc()
    except (OSError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    built = build_news(doc)
    if not built:
        live = doc["live"]
        print(
            f"[skip] 未过线：24h={live.get('chg_24h_pct')}% "
            f"drop1h={live.get('drop_1h_from_high_pct')}% chg1h={live.get('chg_1h_pct')}% "
            f"week_drop={live.get('drop_from_week_high_pct')}% "
            f"liq1h={live.get('liquidation_usd_1h')} liq24={live.get('liquidation_usd_24h')} "
            f"rank={live.get('liquidation_rank_24h')}"
        )
        return 0
    msg_id, text = built
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
        return 1
    print(f"[pushed] {msg_id}")
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-200:]
    save_state(state)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"{stamp}\t{msg_id}\tok\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
