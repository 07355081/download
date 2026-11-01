#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""XRP Ledger 链上手续费 → Lark 新闻体。

临近免费源：
  - CoinMetrics community FeeTotNtv：日销毁 XRP、90 日均值
  - DefiLlama summary/fees/xrpl：日手续费美元（methodology：用户 gas，全部销毁）

触发（只发最新，不回扫，不发日常）：
  1) 最新完整日 90 日均值创序列新低
  2) 最新完整日美元手续费创序列新低
超过 3 天的最新日不发。活跃地址不做（Glassnode 无免费同源）。
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
OUT_PATH = HERE / "output" / "json" / "xrp_fees.json"
STATE_PATH = HERE / "output" / "xrp_fees_lark.json"
LOG_PATH = HERE / "flash.log"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/xrp-fees"
CM_API = (
    "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
    "?assets=xrp&metrics=FeeTotNtv&frequency=1d&start_time=2024-11-01&page_size=10000"
)
LLAMA_API = "https://api.llama.fi/summary/fees/xrpl?dataType=dailyFees"
WINDOW = 90
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


def _get_json(url: str, ua: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_cm_days() -> list[dict[str, Any]]:
    payload = _get_json(CM_API, "dashboard-data/xrp-fees")
    rows = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        raw = item.get("FeeTotNtv")
        if raw in (None, ""):
            continue
        rows.append({"day": str(item.get("time") or "")[:10], "fee_xrp": float(raw)})
    rows.sort(key=lambda item: item["day"])
    if len(rows) < WINDOW + 5:
        raise RuntimeError(f"CoinMetrics 日手续费只有 {len(rows)} 天")
    return rows


def fetch_llama_days() -> list[dict[str, Any]]:
    payload = _get_json(LLAMA_API, "dashboard-data/xrp-fees")
    chart = payload.get("totalDataChart") or []
    rows = []
    for point in chart:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        ts, val = point[0], point[1]
        if val in (None, ""):
            continue
        day = datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d")
        rows.append({"day": day, "fee_usd": float(val)})
    # 当日若仍在滚动更新，去掉与倒数第二天相同值的重复尾点（Llama 偶发）
    rows.sort(key=lambda item: item["day"])
    if len(rows) >= 2 and abs(rows[-1]["fee_usd"] - rows[-2]["fee_usd"]) < 1e-9:
        # 若最新日历日是「今天」且数值与昨日完全相同，倾向丢弃未闭合日
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if rows[-1]["day"] == today:
            rows = rows[:-1]
    if len(rows) < 30:
        raise RuntimeError(f"DefiLlama XRPL 日手续费只有 {len(rows)} 天")
    return rows


def with_sma(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for i, row in enumerate(rows):
        item = dict(row)
        if i >= WINDOW - 1:
            window = rows[i - WINDOW + 1 : i + 1]
            item["sma90_xrp"] = sum(point["fee_xrp"] for point in window) / WINDOW
        out.append(item)
    return out


def window_sums(usd_rows: list[dict[str, Any]], day: str) -> dict[str, float]:
    by_day = {r["day"]: r["fee_usd"] for r in usd_rows}
    days = [r["day"] for r in usd_rows if r["day"] <= day]
    if not days or days[-1] != day:
        raise RuntimeError(f"DefiLlama 无完整日 {day}")
    last7 = days[-7:]
    last30 = days[-30:]
    return {
        "fee_1d_usd": by_day[day],
        "fee_7d_usd": sum(by_day[d] for d in last7),
        "fee_30d_usd": sum(by_day[d] for d in last30),
    }


def fmt_usd(n: float) -> str:
    if n >= 10000:
        return f"{n / 10000:.1f} 万美元".replace(".0 ", " ")
    if n >= 1000:
        return f"约 {n:,.0f} 美元"
    return f"约 {n:.0f} 美元"


def save_doc(
    cm_rows: list[dict[str, Any]],
    usd_rows: list[dict[str, Any]],
    latest_cm: dict[str, Any],
    peak: dict[str, Any],
    sums: dict[str, float],
) -> None:
    burn_by_day = {r["day"]: r["fee_xrp"] for r in cm_rows}
    payload = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "CoinMetrics FeeTotNtv + DefiLlama summary/fees/xrpl",
        "note": (
            "90 日均值新低或美元日费新低才发新闻；不发日常、不回扫。"
            "DefiLlama：用户 gas，费用全部销毁。非 Glassnode 活跃地址。"
        ),
        "latest": {
            "day": latest_cm["day"],
            "fee_xrp": round(latest_cm["fee_xrp"], 4),
            "sma90_xrp": round(latest_cm["sma90_xrp"], 4),
            "fee_1d_usd": round(sums["fee_1d_usd"], 2),
            "fee_7d_usd": round(sums["fee_7d_usd"], 2),
            "fee_30d_usd": round(sums["fee_30d_usd"], 2),
        },
        "peak_sma90": {
            "day": peak["day"],
            "sma90_xrp": round(peak["sma90_xrp"], 4),
        },
        "data": [
            {
                "day": row["day"],
                "fees_usd": round(row["fee_usd"], 2),
                "value": round(row["fee_usd"], 2),
                **(
                    {"fee_xrp": round(burn_by_day[row["day"]], 4)}
                    if row["day"] in burn_by_day
                    else {}
                ),
            }
            for row in usd_rows
        ],
        "days_xrp": [
            {
                "day": row["day"],
                "fee_xrp": round(row["fee_xrp"], 4),
                **({"sma90_xrp": round(row["sma90_xrp"], 4)} if "sma90_xrp" in row else {}),
            }
            for row in cm_rows
        ],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)


def build_sma_news(latest: dict[str, Any], peak: dict[str, Any], sums: dict[str, float]) -> str:
    drop = (peak["sma90_xrp"] - latest["sma90_xrp"]) / peak["sma90_xrp"] * 100
    return (
        f"据吴说数据中心统计，截至 {latest['day']}，XRP Ledger 日手续费（销毁量）的 90 日均值约 "
        f"{latest['sma90_xrp']:.0f} XRP，为本序列新低。"
        f"序列内高点为 {peak['day']} 约 {peak['sma90_xrp']:.0f} XRP，较高点下降约 {drop:.1f}%。"
        f"当日销毁约 {latest['fee_xrp']:.0f} XRP；"
        f"美元口径当日手续费 {fmt_usd(sums['fee_1d_usd'])}，"
        f"近 7 日合计 {fmt_usd(sums['fee_7d_usd'])}，近 30 日合计 {fmt_usd(sums['fee_30d_usd'])}。"
        f"\n{PAGE}"
    )


def build_usd_low_news(day: str, burn_xrp: float | None, sums: dict[str, float], prior_min: dict[str, Any]) -> str:
    burn = f"当日销毁约 {burn_xrp:.0f} XRP；" if burn_xrp is not None else ""
    return (
        f"据吴说数据中心统计，截至 {day}，XRP Ledger 链上日手续费约 {sums['fee_1d_usd']:.0f} 美元，"
        f"创本序列新低（此前低点为 {prior_min['day']} 约 {prior_min['fee_usd']:.0f} 美元）。"
        f"{burn}"
        f"近 7 日合计 {fmt_usd(sums['fee_7d_usd'])}，近 30 日合计 {fmt_usd(sums['fee_30d_usd'])}。"
        f"XRPL 单笔基础手续费极低且费用销毁，日交易量很高时手续费总额仍有限。"
        f"\n{PAGE}"
    )


def push_one(msg_id: str, text: str, push: bool, force: bool) -> int:
    text = as_news(text)
    print("---")
    print(f"id={msg_id}")
    print(text)
    if not push:
        print("\n[dry-run] 未推送")
        return 0
    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    state = load_state()
    if msg_id in state["sentIds"] and not force:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--force-kind",
        choices=("sma90", "usd"),
        default=None,
        help="配合 --force 指定推哪一类",
    )
    args = parser.parse_args()
    try:
        cm_rows = with_sma(fetch_cm_days())
        usd_rows = fetch_llama_days()
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] 拉数失败: {exc}", file=sys.stderr)
        return 1

    sma_rows = [row for row in cm_rows if "sma90_xrp" in row]
    latest_cm = sma_rows[-1]
    prior_sma = sma_rows[:-1]
    prior_sma_min = min(prior_sma, key=lambda row: row["sma90_xrp"])
    peak = max(sma_rows, key=lambda row: row["sma90_xrp"])
    is_sma_low = latest_cm["sma90_xrp"] < prior_sma_min["sma90_xrp"] - 1e-6

    # 美元序列以 Llama 最新完整日为准；与 CM 日对齐取销毁量
    latest_usd = usd_rows[-1]
    prior_usd = usd_rows[:-1]
    prior_usd_min = min(prior_usd, key=lambda row: row["fee_usd"])
    is_usd_low = latest_usd["fee_usd"] < prior_usd_min["fee_usd"] - 1e-9

    try:
        sums = window_sums(usd_rows, latest_usd["day"])
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    burn_map = {r["day"]: r["fee_xrp"] for r in cm_rows}
    burn_today = burn_map.get(latest_usd["day"])
    save_doc(cm_rows, usd_rows, latest_cm, peak, sums)

    age_cm = (datetime.now(timezone.utc).date() - datetime.strptime(latest_cm["day"], "%Y-%m-%d").date()).days
    age_usd = (datetime.now(timezone.utc).date() - datetime.strptime(latest_usd["day"], "%Y-%m-%d").date()).days
    print(
        f"[xrp-fees] cm={latest_cm['day']} fee_xrp={latest_cm['fee_xrp']:.1f} "
        f"sma90={latest_cm['sma90_xrp']:.1f} new_sma_low={is_sma_low} age_cm={age_cm}d | "
        f"usd={latest_usd['day']} fee_usd={latest_usd['fee_usd']:.0f} "
        f"7d={sums['fee_7d_usd']:.0f} 30d={sums['fee_30d_usd']:.0f} "
        f"new_usd_low={is_usd_low} prior_min={prior_usd_min['day']} {prior_usd_min['fee_usd']:.0f} "
        f"age_usd={age_usd}d"
    )

    rc = 0
    force_sma = args.force and args.force_kind in (None, "sma90")
    force_usd = args.force and args.force_kind in (None, "usd")

    if age_cm > 3 and not args.force:
        print("[skip] CM 最新日超过 3 天，不发 sma90")
    elif is_sma_low or force_sma:
        try:
            sma_sums = window_sums(usd_rows, latest_cm["day"])
        except RuntimeError:
            sma_sums = sums
        r = push_one(
            f"xrp-fee-sma90-low-{latest_cm['day']}",
            build_sma_news(latest_cm, peak, sma_sums),
            args.push,
            args.force,
        )
        rc = r if r else rc

    if age_usd > 3 and not args.force:
        print("[skip] Llama 最新日超过 3 天，不发 usd")
    elif is_usd_low or force_usd:
        r = push_one(
            f"xrp-fee-usd-low-{latest_usd['day']}",
            build_usd_low_news(latest_usd["day"], burn_today, sums, prior_usd_min),
            args.push,
            args.force,
        )
        rc = r if r else rc

    if not is_sma_low and not is_usd_low and not args.force:
        print("[skip] 90 日均值与美元日费均未创新低")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
