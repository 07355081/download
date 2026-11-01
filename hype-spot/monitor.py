#!/usr/bin/env python3
"""HYPE 现货创历史新高 → Lark 新闻。

价格用 OKX 公开 HYPE-USDT，不占付费额度。
只在现价或 24 小时最高价高于此前每一根日 K 时发一条，正文可直接发稿。
已经过去的高点不补发。

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
OUT_PATH = HERE / "output" / "json" / "hype_spot.json"
STATE_PATH = HERE / "output" / "lark_state.json"
LOG_PATH = HERE / "monitor.log"
LOCAL_MIRROR = ROOT.parent / "data-dashboard" / "public" / "json" / "hype-spot" / "hype_spot.json"
JSON_URL = "https://data.wublock123.com/dashboard/hyperliquid"
PAGE = "https://data.wublock123.com/dashboard/hyperliquid"
OKX_TICKER = "https://www.okx.com/api/v5/market/ticker?instId=HYPE-USDT"
OKX_CANDLES = "https://www.okx.com/api/v5/market/candles?instId=HYPE-USDT&bar=1D&limit=100"
UA = "dashboard-data/hype-spot"
TZ_CN = timezone(timedelta(hours=8))
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
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def http_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("OKX 返回不是对象")
    if str(payload.get("code")) != "0":
        raise RuntimeError(f"OKX {payload.get('code')} {payload.get('msg')}")
    return payload


def day_of(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, TZ_CN).strftime("%Y-%m-%d")


def okx_days() -> list[dict[str, Any]]:
    after: str | None = None
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _ in range(20):
        url = OKX_CANDLES if not after else f"{OKX_CANDLES}&after={after}"
        payload = http_json(url)
        batch = payload.get("data") or []
        if not batch:
            break
        for bar in batch:
            if not isinstance(bar, list) or len(bar) < 5:
                continue
            day = day_of(int(bar[0]))
            if day in seen:
                continue
            seen.add(day)
            rows.append({
                "day": day,
                "open": float(bar[1]),
                "high": float(bar[2]),
                "low": float(bar[3]),
                "close": float(bar[4]),
            })
        after = str(batch[-1][0])
        if len(batch) < 100:
            break
    rows.sort(key=lambda item: item["day"])
    if len(rows) < 30:
        raise RuntimeError(f"OKX 日线只有 {len(rows)} 根")
    return rows


def px(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def news_text(session_high: float, last: float, chg: float) -> str:
    word = "上涨" if chg >= 0 else "下跌"
    return (
        f"据吴说数据中心统计，OKX 行情显示，HYPE 今日最高升至 {px(session_high)} 美元，"
        f"刷新历史新高，现报约 {px(last)} 美元，24 小时{word}约 {abs(chg):.1f}%。"
        f"\n{PAGE}"
    )


def write_json(payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    paths = [OUT_PATH]
    if LOCAL_MIRROR.parents[2].is_dir():
        paths.append(LOCAL_MIRROR)
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": [], "ath": 0.0}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": [], "ath": 0.0}
    ids = doc.get("sentIds") if isinstance(doc, dict) else None
    ath = doc.get("ath") if isinstance(doc, dict) else 0
    return {
        "sentIds": [item for item in ids if isinstance(item, str)] if isinstance(ids, list) else [],
        "ath": float(ath) if isinstance(ath, (int, float)) else 0.0,
    }


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = now_iso()
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def gen_lark_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark_text(webhook: str, secret: str | None, text: str) -> None:
    text = as_news(text)
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


def append_log(stamp: str, msg_id: str, ok: bool, text: str) -> None:
    line = json.dumps({"ts": stamp, "id": msg_id, "ok": ok, "text": text}, ensure_ascii=False)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()

    days = okx_days()
    ticker = http_json(OKX_TICKER)
    spot = (ticker.get("data") or [{}])[0]
    last = float(spot.get("last") or 0)
    high24 = float(spot.get("high24h") or 0)
    open24 = float(spot.get("open24h") or 0)
    if last <= 0 or high24 <= 0 or open24 <= 0:
        raise RuntimeError("OKX 行情缺字段")
    chg = (last / open24 - 1) * 100
    session = max(last, high24)
    today = datetime.now(TZ_CN).strftime("%Y-%m-%d")
    prior_rows = [row for row in days if row["day"] < today]
    if not prior_rows:
        raise RuntimeError("没有更早的日线，不能判断新高")
    prior = max(prior_rows, key=lambda row: row["high"])
    state = load_state()
    baseline = max(float(prior["high"]), float(state.get("ath") or 0))
    payload = {
        "updated_at": now_iso(),
        "source": "OKX HYPE-USDT 公开行情",
        "note": "只在最新高点高于此前每一根日 K 时发新闻。已过去的高点不补发。",
        "live": {
            "asof": now_iso(),
            "okx_price": round(last, 3),
            "high_24h": round(high24, 3),
            "chg_24h_pct": round(chg, 1),
        },
        "prior_ath": {"day": prior["day"], "high": round(float(prior["high"]), 3)},
        "days": prior_rows,
    }
    write_json(payload)
    print(
        f"[hype-spot] price={px(last)} high24={px(high24)} chg={chg:.1f}% "
        f"prior={prior['day']} {px(float(prior['high']))}"
    )

    msg_id = f"hype-spot-ath-{today}"
    if session <= baseline + 1e-4:
        print(f"[skip] 未创历史新高（现高 {px(session)}，此前 {px(baseline)}）")
        state["ath"] = baseline
        save_state(state)
        return 0

    text = news_text(session, last, chg)
    print("---")
    print(f"id={msg_id} already={msg_id in state['sentIds']}")
    print(text)
    if msg_id in state["sentIds"]:
        print("[skip] 今天这条已经发过")
        return 0
    if not args.push:
        print("[dry-run] 未推送")
        return 0

    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    try:
        send_lark_text(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Lark 失败 {msg_id}: {exc}", file=sys.stderr)
        append_log(stamp, msg_id, False, text)
        return 1
    print(f"[pushed] {msg_id}")
    state["sentIds"] = (state["sentIds"] + [msg_id])[-200:]
    state["ath"] = session
    save_state(state)
    append_log(stamp, msg_id, True, text)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
