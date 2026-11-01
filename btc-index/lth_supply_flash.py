#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""比特币 LTH 供应量（持仓>155 天）→ Lark 新闻体。

读看板 CoinGlass 序列 bitcoin-long-term-holder-supply。
口径：跌破前高后再站上 → 连续未跌回该前高的天数；较 90 日峰值回撤；历史分位。

过线才发（非每日流水；只发最新一日）：
  - 连续站上前高 ≥ 60 天，且历史分位 ≥ 95%，且较 90 日峰值回撤 ≤ -2%
    → 同一峰值日只发一次
  - 或连续天数触及 60/90/120/150/180/210/240/270/300/365
    → 同一轮站上（start+里程碑）只发一次

用法：
  python lth_supply_flash.py
  python lth_supply_flash.py --push
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
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    Path("/root/dashboard/public/json/btc-index/coinglass/bitcoin-long-term-holder-supply.json"),
    HERE / "output" / "json" / "coinglass" / "bitcoin-long-term-holder-supply.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "btc-index" / "coinglass" / "bitcoin-long-term-holder-supply.json",
]
STATE_PATH = HERE / "output" / "lth_supply_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
PAGE = "https://data.wublock123.com/dashboard/btc-indicators/bitcoin-long-term-holder-supply"
JSON_URL = "https://data.wublock123.com/json/btc-index/coinglass/bitcoin-long-term-holder-supply.json"
TZ_CN = timezone(timedelta(hours=8))
FRESH_DAYS = 5
MIN_STREAK = 60
MIN_PCTILE = 95.0
MAX_DRAWDOWN_PCT = -2.0  # 相对 90 日峰值
LOOKBACK_PEAK = 90
MILESTONES = (60, 90, 120, 150, 180, 210, 240, 270, 300, 365)
FLOOR_TOL = 0.995


def _ensure_lark_copy() -> None:
    folder = Path(__file__).resolve().parent
    while folder != folder.parent and not (folder / "lark_copy.py").is_file():
        folder = folder.parent
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))


_ensure_lark_copy()
from lark_copy import as_news  # noqa: E402


def env() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            out[key] = os.environ[key]
    return out


def send(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": as_news(text)}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(
            hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
        ).decode()
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode() or "{}")
    if str(body.get("code", "0")) != "0":
        raise RuntimeError(body)


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


def resolve_json() -> Path | None:
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    return None


def load_series() -> tuple[list[datetime], list[float]]:
    path = resolve_json()
    if path is not None:
        doc = json.loads(path.read_text(encoding="utf-8"))
    else:
        req = urllib.request.Request(JSON_URL, headers={"User-Agent": "dashboard-data/lth-supply"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
    data = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError("LTH JSON 无效")
    ts_list = data.get("time_list") or []
    raw = data.get("data_list") or []
    if len(ts_list) != len(raw) or not ts_list:
        raise RuntimeError("LTH 序列为空")
    times: list[datetime] = []
    values: list[float] = []
    for t, row in zip(ts_list, raw):
        if isinstance(row, dict):
            v = row.get("long_term_holder_supply")
        else:
            v = row
        if v is None:
            continue
        times.append(datetime.fromtimestamp(int(t) / 1000, tz=timezone.utc))
        values.append(float(v))
    return times, values


def reclaim_events(values: list[float], n: int) -> list[tuple[int, float]]:
    ath = values[0]
    pending: float | None = None
    events: list[tuple[int, float]] = []
    for i in range(1, n):
        v = values[i]
        if v < ath:
            pending = ath
        elif pending is not None and v >= pending:
            events.append((i, pending))
            pending = None
        ath = max(ath, v)
    return events


def regime_at(values: list[float], t: int) -> tuple[float, int, int] | None:
    """返回 (前高 floor, 站上起始下标, 连续天数)。"""
    events = reclaim_events(values, t + 1)
    smin = [0.0] * (t + 1)
    cur = values[t]
    for i in range(t, -1, -1):
        cur = min(cur, values[i])
        smin[i] = cur
    valid: list[tuple[float, int, int]] = []
    for start, floor in events:
        if start <= t and smin[start] >= floor - 1e-6:
            valid.append((floor, start, t - start + 1))
    if not valid:
        return None
    max_fl = max(v[0] for v in valid)
    pool = [v for v in valid if v[0] >= max_fl * FLOOR_TOL]
    return max(pool, key=lambda v: (v[2], v[0]))


def wan(v: float) -> str:
    return f"{v / 1e4:,.2f}".replace(",", ",")


def pct_text(v: float) -> str:
    sign = "+" if v > 0 else ""
    r1 = round(v, 2)
    if abs(r1 - round(r1)) < 0.005:
        return f"{sign}{int(round(r1))}%"
    return f"{sign}{r1:.2f}%"


def cn_date(dt: datetime) -> str:
    local = dt.astimezone(TZ_CN)
    return f"{local.year} 年 {local.month} 月 {local.day} 日"


def build_news(
    asof: datetime,
    value: float,
    floor: float,
    start: datetime,
    streak: int,
    peak: float,
    pctile: float,
) -> str:
    above = value - floor
    above_pct = (value / floor - 1.0) * 100.0 if floor else 0.0
    drop = value - peak
    drop_pct = (value / peak - 1.0) * 100.0 if peak else 0.0
    parts = [
        f"据吴说数据中心统计，比特币长期持有者（LTH，持仓超过 155 天的地址）供应量"
        f"自 {cn_date(start)}稳定站上该前高以来，已连续 {streak} 天未跌回其下方，"
        f"截至 {cn_date(asof)}报 {wan(value)} 万枚，较该前高高出 {wan(above)} 万枚（{pct_text(above_pct)}）。",
    ]
    if drop < 0:
        parts.append(
            f"近日回落 {wan(abs(drop))} 万枚（{pct_text(drop_pct)}），"
            f"但仍处历史以来的 {pctile:.0f}% 分位。"
        )
    else:
        parts.append(f"现处历史以来的 {pctile:.0f}% 分位。")
    parts.append("口径为 CoinGlass LTH Supply（看板 btc-indicators）。")
    return "".join(parts) + f"\n{PAGE}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    args = ap.parse_args()

    times, values = load_series()
    t = len(values) - 1
    asof = times[t]
    age = (datetime.now(timezone.utc) - asof).total_seconds() / 86400.0
    if age > FRESH_DAYS:
        print(f"[skip] 数据过旧 asof={asof.date()} age={age:.1f}d")
        return 0

    reg = regime_at(values, t)
    if reg is None:
        print("[skip] 无有效站上前高区间")
        return 0
    floor, start_i, streak = reg
    start = times[start_i]
    value = values[t]
    window = values[max(0, t - LOOKBACK_PEAK + 1) : t + 1]
    peak = max(window)
    peak_i = max(0, t - LOOKBACK_PEAK + 1) + window.index(peak)
    peak_date = times[peak_i].date().isoformat()
    pctile = sum(1 for x in values[: t + 1] if x <= value) / (t + 1) * 100.0
    drop_pct = (value / peak - 1.0) * 100.0 if peak else 0.0

    state = load_state()
    sent = set(state["sentIds"])
    msg_id: str | None = None
    reason = ""

    if (
        streak >= MIN_STREAK
        and pctile >= MIN_PCTILE
        and drop_pct <= MAX_DRAWDOWN_PCT
    ):
        cand = f"lth-pullback-{peak_date}"
        if cand not in sent:
            msg_id = cand
            reason = f"pullback peak={peak_date} drop={drop_pct:.2f}%"

    if msg_id is None and streak in MILESTONES:
        cand = f"lth-streak-{times[start_i].date().isoformat()}-{streak}"
        if cand not in sent:
            msg_id = cand
            reason = f"milestone {streak}d"

    if msg_id is None:
        print(
            f"[skip] 未过线 streak={streak} pctile={pctile:.1f} drop={drop_pct:.2f}% "
            f"asof={asof.date()}"
        )
        return 0

    text = build_news(asof, value, floor, start, streak, peak, pctile)
    print(msg_id, reason)
    print(text)

    if not args.push:
        print("[dry-run] 未推送")
        return 0
    conf = env()
    webhook = (conf.get("LARK_ALERT_WEBHOOK") or "").strip()
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 1
    send(webhook, (conf.get("LARK_ALERT_SECRET") or "").strip() or None, text)
    state["sentIds"] = list(sent | {msg_id})
    save_state(state)
    print(f"[pushed] {msg_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
