#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""美国官方 CPI → JSON，近 36 个月新高或新低才发新闻。

未季调 CPI、未季调核心 CPI、季调后核心 CPI 都用公开月度指数自己算年率月率。
不含市场预期。第一次只把当前月份记成基线，不发已经公布的月份。
之后只看最新月份：必须是上一个自然月或更新，且四项里至少一项创近 36 个月新高或新低。
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
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_PATH = HERE / "output" / "json" / "us_cpi.json"
STATE_PATH = HERE / "output" / "us_cpi_lark.json"
LOG_PATH = HERE / "flash.log"
JSON_URL = "https://data.wublock123.com/json/us-cpi/us_cpi.json"
API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
SERIES = (
    ("nsa", "CUUR0000SA0"),
    ("core_nsa", "CUUR0000SA0L1E"),
    ("core_sa", "CUSR0000SA0L1E"),
)
FIELDS = (
    ("nsa_yoy", "未季调 CPI 年率"),
    ("nsa_mom", "未季调 CPI 月率"),
    ("core_nsa_yoy", "未季调核心 CPI 年率"),
    ("core_sa_mom", "季调后核心 CPI 月率"),
)
WINDOW = 36
TZ_CN = timezone(timedelta(hours=8))
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
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode("utf-8") or "{}")
    if str(body.get("code", "0")) != "0":
        raise RuntimeError(body)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": [], "baselineMonth": None}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": [], "baselineMonth": None}
    if not isinstance(doc, dict):
        return {"sentIds": [], "baselineMonth": None}
    ids = doc.get("sentIds")
    baseline = doc.get("baselineMonth")
    return {
        "sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else [],
        "baselineMonth": baseline if isinstance(baseline, str) else None,
    }


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def fetch_indexes() -> dict[str, dict[str, Decimal]]:
    end_year = datetime.now(TZ_CN).year
    body = json.dumps({
        "seriesid": [code for _, code in SERIES],
        "startyear": str(end_year - 5),
        "endyear": str(end_year),
    }).encode()
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "dashboard-data/us-cpi"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=40) as resp:
        doc = json.loads(resp.read().decode("utf-8"))
    if doc.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"CPI 接口失败: {doc.get('message')}")
    by_id = {row.get("seriesID"): row for row in (doc.get("Results") or {}).get("series") or []}
    out: dict[str, dict[str, Decimal]] = {}
    for name, code in SERIES:
        series = by_id.get(code) or {}
        months: dict[str, Decimal] = {}
        for row in series.get("data") or []:
            period = str(row.get("period") or "")
            if not period.startswith("M") or period == "M13":
                continue
            raw = str(row.get("value") or "").strip()
            if raw in {"", "-"}:
                continue
            months[f"{row['year']}-{period[1:]}"] = Decimal(raw)
        if len(months) < WINDOW + 13:
            raise RuntimeError(f"{name} 月份不足")
        out[name] = months
    return out


def shift_month(month: str, back: int) -> str:
    year, mon = map(int, month.split("-"))
    mon -= back
    while mon <= 0:
        mon += 12
        year -= 1
    return f"{year:04d}-{mon:02d}"


def rate(cur: Decimal, old: Decimal) -> Decimal:
    return (cur / old - Decimal(1)) * Decimal(100)


def build_rows(indexes: dict[str, dict[str, Decimal]]) -> list[dict[str, Any]]:
    months = sorted(set.intersection(*(set(table) for table in indexes.values())))
    rows: list[dict[str, Any]] = []
    for month in months:
        prev = shift_month(month, 1)
        year_ago = shift_month(month, 12)
        if prev not in indexes["nsa"] or year_ago not in indexes["nsa"]:
            continue
        if year_ago not in indexes["core_nsa"] or prev not in indexes["core_sa"]:
            continue
        rows.append({
            "month": month,
            "nsa_yoy": rate(indexes["nsa"][month], indexes["nsa"][year_ago]),
            "nsa_mom": rate(indexes["nsa"][month], indexes["nsa"][prev]),
            "core_nsa_yoy": rate(indexes["core_nsa"][month], indexes["core_nsa"][year_ago]),
            "core_sa_mom": rate(indexes["core_sa"][month], indexes["core_sa"][prev]),
        })
    if len(rows) <= WINDOW:
        raise RuntimeError("可比较月份不足 36 个")
    return rows


def one_decimal(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def previous_month(today: datetime) -> str:
    year, month = today.year, today.month - 1
    if month == 0:
        year, month = year - 1, 12
    return f"{year:04d}-{month:02d}"


def extremes(rows: list[dict[str, Any]]) -> list[tuple[str, str, str | None]]:
    latest = rows[-1]
    prior = rows[-(WINDOW + 1):-1]
    hits: list[tuple[str, str, str | None]] = []
    for key, _label in FIELDS:
        current = latest[key]
        highest = max(row[key] for row in prior)
        lowest = min(row[key] for row in prior)
        if current > highest:
            anchor = next((row["month"] for row in reversed(rows[:-1]) if row[key] >= current), None)
            hits.append((key, "高", anchor))
        elif current < lowest:
            anchor = next((row["month"] for row in reversed(rows[:-1]) if row[key] <= current), None)
            hits.append((key, "低", anchor))
    return hits


def month_label(month: str) -> str:
    year, mon = month.split("-")
    return f"{year} 年 {int(mon)} 月"


def build_news(latest: dict[str, Any], hits: list[tuple[str, str, str | None]]) -> str:
    bits = []
    hit_map = {key: (kind, anchor) for key, kind, anchor in hits}
    for key, label in FIELDS:
        number = one_decimal(latest[key])
        clause = f"{label}录得 {number}%"
        if key in hit_map:
            kind, anchor = hit_map[key]
            if anchor:
                touched = "不低于" if kind == "高" else "不高于"
                clause += f"，为近 36 个月新{kind}，上一次{touched}该水平是在 {month_label(anchor)}"
            else:
                clause += f"，为本序列新{kind}"
        bits.append(clause)
    return (
        f"吴说获悉，据吴说数据中心统计，美国 {month_label(latest['month'])}，"
        f"{'，'.join(bits)}。\n{JSON_URL}"
    )


def save_doc(rows: list[dict[str, Any]]) -> None:
    latest = rows[-1]
    payload = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest_month": latest["month"],
        "note": "美国官方消费者价格指数。年率与月率由指数计算，保留一位小数。不含市场预期。",
        "latest": {
            "month": latest["month"],
            "nsa_cpi_yoy_pct": one_decimal(latest["nsa_yoy"]),
            "nsa_cpi_mom_pct": one_decimal(latest["nsa_mom"]),
            "nsa_core_yoy_pct": one_decimal(latest["core_nsa_yoy"]),
            "sa_core_mom_pct": one_decimal(latest["core_sa_mom"]),
        },
        "rows": [
            {
                "month": row["month"],
                "nsa_cpi_yoy_pct": one_decimal(row["nsa_yoy"]),
                "nsa_cpi_mom_pct": one_decimal(row["nsa_mom"]),
                "nsa_core_yoy_pct": one_decimal(row["core_nsa_yoy"]),
                "sa_core_mom_pct": one_decimal(row["core_sa_mom"]),
            }
            for row in rows
        ],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    try:
        rows = build_rows(fetch_indexes())
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError, KeyError, ValueError) as exc:
        print(f"[ERROR] 拉数失败: {exc}", file=sys.stderr)
        return 1
    save_doc(rows)
    latest = rows[-1]
    shown = " ".join(f"{key}={one_decimal(latest[key])}" for key, _label in FIELDS)
    print(f"[us-cpi] month={latest['month']} {shown}")
    state = load_state()
    baseline = state.get("baselineMonth")
    if not baseline:
        state["baselineMonth"] = latest["month"]
        print(f"[skip] 首轮建基线 {latest['month']}，不发已公布月份")
        if args.push:
            save_state(state)
        return 0
    if latest["month"] <= baseline:
        print("[skip] 没有更新的月份")
        return 0
    fresh_from = previous_month(datetime.now(TZ_CN))
    hits = extremes(rows)
    if latest["month"] < fresh_from or not hits:
        state["baselineMonth"] = latest["month"]
        reason = "月份已过发布窗口" if latest["month"] < fresh_from else "未创近 36 个月新高或新低"
        print(f"[skip] {reason}")
        if args.push:
            save_state(state)
        return 0
    msg_id = f"us-cpi-{latest['month']}"
    text = build_news(latest, hits)
    print("---")
    print(f"id={msg_id}")
    print(as_news(text))
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0
    if msg_id in state["sentIds"]:
        state["baselineMonth"] = latest["month"]
        save_state(state)
        print(f"[skip] already {msg_id}")
        return 0
    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    try:
        send_lark(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Lark 失败: {exc}", file=sys.stderr)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"=== {stamp} {msg_id} ok=False {exc}\n{text}\n\n")
        return 1
    print(f"[pushed] {msg_id}")
    state["baselineMonth"] = latest["month"]
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-200:]
    save_state(state)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} {msg_id} ok=True\n{text}\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
