#!/usr/bin/env python3
"""GUA / Superfortune（CoinGecko）→ Lark 新闻体，只推最新过线，不回扫历史。

过线：24 小时 |涨跌| ≥ 20%，或较近 7 日高点累计跌幅 ≥ 50%。
不过线不发日常流水。

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
    HERE / "output" / "json" / "gua_spot.json",
    Path("/root/dashboard/public/json/gua-spot/gua_spot.json"),
]
STATE_PATH = HERE / "output" / "gua_spot_lark.json"
LOG_PATH = HERE / "flash.log"
JSON_URL = "https://data.wublock123.com/dashboard/lark-feeds/gua-spot"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/gua-spot"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
MOVE_PCT = 20.0
WEEK_DROP_PCT = 50.0

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


def append_log(stamp: str, msg_id: str, ok: bool, text: str) -> None:
    line = json.dumps({"ts": stamp, "id": msg_id, "ok": ok, "text": text}, ensure_ascii=False)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def load_doc() -> dict[str, Any]:
    for path in JSON_CANDIDATES:
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("live"), dict):
                print(f"[gua-spot] json={path}")
                return doc
    raise FileNotFoundError("找不到 gua_spot.json")


def pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def price_s(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 1:
        return f"{value:.4f}"
    if value >= 0.1:
        return f"{value:.4f}"
    return f"{value:.6f}".rstrip("0").rstrip(".")


def mcap_s(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 1e8:
        return f"{value / 1e8:.2f} 亿美元"
    if value >= 1e4:
        return f"{value / 1e4:.0f} 万美元"
    return f"{value:.0f} 美元"


def build_news(doc: dict[str, Any]) -> tuple[str, str] | None:
    live = doc["live"]
    chg = live.get("chg_24h_pct")
    chg_f = float(chg) if isinstance(chg, (int, float)) else None
    week_drop = live.get("drop_from_week_high_pct")
    week_drop_f = float(week_drop) if isinstance(week_drop, (int, float)) else None
    reasons: list[str] = []
    if chg_f is not None and abs(chg_f) >= MOVE_PCT:
        reasons.append(f"过去 24 小时约 {pct(chg_f)}")
    if week_drop_f is not None and week_drop_f <= -WEEK_DROP_PCT:
        reasons.append(f"较近 7 日高点累计约 {pct(week_drop_f)}")
    if not reasons:
        return None
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
    day = datetime.now(TZ_CN).strftime("%Y-%m-%d")
    name = str(doc.get("name") or "Superfortune")
    symbol = str(doc.get("symbol") or "GUA")
    low_bit = ""
    if live.get("low_24h"):
        low_bit = f"过去 24 小时最低约触及 {price_s(float(live['low_24h']))} 美元。"
    week_bit = ""
    if live.get("week_high") and live.get("week_high_day"):
        week_bit = (
            f"近 7 日高点约 {price_s(float(live['week_high']))} 美元"
            f"（{live['week_high_day']}），"
        )
    text = (
        f"据吴说数据中心统计，截至 {stamp}（UTC+8），CoinGecko 口径 {name}（{symbol}）现报约 "
        f"{price_s(float(live['price']))} 美元，{'，'.join(reasons)}。"
        f"流通市值约 {mcap_s(live.get('market_cap_usd'))}。"
        f"{week_bit}{low_bit}"
        f"口径为 CoinGecko 公开行情。"
        f"\n{PAGE}"
    )
    return f"gua-cg-{day}", text


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
            f"week_drop={live.get('drop_from_week_high_pct')}% "
            f"(线 |24h|>={MOVE_PCT:g}% 或 7日高点跌幅<={-WEEK_DROP_PCT:g}%)"
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
