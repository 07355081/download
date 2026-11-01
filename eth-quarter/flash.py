#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""以太坊季度活动 + 月交易笔数 → Lark 新闻体。

GrowThePie 免费 fundamentals 只有滚动约 90 天，没有月活，也没有贷款。
本脚本把拉到的日子并进本地序列，避免窗口滑掉。
交易笔数、TPS、日均活跃地址、链上手续费、应用手续费、季末 TVL，
以及这些指标在 GrowThePie 链样本前五名中的占比。
季度：只发最近一个已收齐的季度，季结束后 14 天内，一条新闻。更早的季度不补。
月度：只发刚收完的完整月。笔数创已存月份新高，或连续至少 4 个完整月
都超过 6000 万笔且创下最长连涨纪录时，发一条新闻。更早的月份不补。
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_PATH = HERE / "output" / "json" / "eth_quarter.json"
STATE_PATH = HERE / "output" / "eth_quarter_lark.json"
LOG_PATH = HERE / "flash.log"
JSON_URL = "https://data.wublock123.com/dashboard/eth-indicators"
PAGE = "https://data.wublock123.com/dashboard/eth-indicators"
API = "https://api.growthepie.com/v1/fundamentals.json"
METRICS = ("txcount", "daa", "fees_paid_usd", "app_fees_usd", "tvl")
CHAIN = "ethereum"
TZ_CN = timezone(timedelta(hours=8))
FRESH_DAYS = 14
MONTH_FRESH_DAYS = 10
MONTH_LINE = 60_000_000.0
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
    req = urllib.request.Request(url, headers={"User-Agent": "dashboard-data/eth-quarter", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_days() -> dict[str, dict[str, dict[str, float]]]:
    if not OUT_PATH.is_file():
        return {}
    try:
        doc = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = doc.get("days") if isinstance(doc, dict) else None
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, dict[str, float]]] = {}
    for day, chains in raw.items():
        if not isinstance(chains, dict):
            continue
        kept: dict[str, dict[str, float]] = {}
        for chain, metrics in chains.items():
            if not isinstance(metrics, dict):
                continue
            row = {key: float(metrics[key]) for key in METRICS if isinstance(metrics.get(key), (int, float))}
            if row:
                kept[str(chain)] = row
        if kept:
            out[str(day)] = kept
    return out


def merge_remote(days: dict[str, dict[str, dict[str, float]]]) -> int:
    doc = http_json(API)
    if not isinstance(doc, list):
        raise RuntimeError("GrowThePie 返回不是列表")
    added = 0
    for item in doc:
        if not isinstance(item, dict):
            continue
        metric = str(item.get("metric_key") or "")
        if metric not in METRICS:
            continue
        day = str(item.get("date") or "")
        chain = str(item.get("origin_key") or "")
        if len(day) != 10 or not chain:
            continue
        try:
            value = float(item.get("value") or 0)
        except (TypeError, ValueError):
            continue
        slot = days.setdefault(day, {}).setdefault(chain, {})
        if slot.get(metric) != value:
            added += 1
        slot[metric] = value
    return added


def save_doc(
    days: dict[str, dict[str, dict[str, float]]],
    latest: dict[str, Any] | None,
    latest_month: dict[str, Any] | None = None,
) -> None:
    payload = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "GrowThePie fundamentals（免费，滚动窗口并入本地）",
        "note": (
            "没有月活和贷款。日均活跃地址不是月活。前五名是这份链样本，不是 Token Terminal。"
            "月交易只发最新完整月的新高或连续≥4 个月超过 6000 万笔；更早月份不补。"
        ),
        "latest_quarter": latest,
        "latest_month": latest_month,
        "days": days,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)


def quarter_of(day: date) -> tuple[int, int]:
    return day.year, (day.month - 1) // 3 + 1


def quarter_bounds(year: int, quarter: int) -> tuple[date, date]:
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    start = date(year, start_month, 1)
    end = date(year, end_month, monthrange(year, end_month)[1])
    return start, end


def parse_day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def chain_days(days: dict[str, dict[str, dict[str, float]]], chain: str, start: date, end: date) -> list[tuple[date, dict[str, float]]]:
    rows = []
    for text, chains in days.items():
        if chain not in chains:
            continue
        day = parse_day(text)
        if start <= day <= end and "txcount" in chains[chain]:
            rows.append((day, chains[chain]))
    rows.sort(key=lambda item: item[0])
    return rows


def complete_quarters(days: dict[str, dict[str, dict[str, float]]], today: date) -> list[tuple[int, int]]:
    found: set[tuple[int, int]] = set()
    for text in days:
        if CHAIN not in days[text] or "txcount" not in days[text][CHAIN]:
            continue
        year, quarter = quarter_of(parse_day(text))
        found.add((year, quarter))
    ready = []
    for year, quarter in sorted(found):
        start, end = quarter_bounds(year, quarter)
        if end >= today:
            continue
        rows = chain_days(days, CHAIN, start, end)
        span = (end - start).days + 1
        if len(rows) < span - 2:
            continue
        if rows[-1][0] < end - timedelta(days=2):
            continue
        ready.append((year, quarter))
    return ready


def sum_metric(rows: list[tuple[date, dict[str, float]]], metric: str) -> float:
    return sum(float(metrics.get(metric) or 0) for _, metrics in rows)


def end_value(days: dict[str, dict[str, dict[str, float]]], chain: str, start: date, end: date, metric: str) -> float | None:
    latest: tuple[date, float] | None = None
    for text, chains in days.items():
        metrics = chains.get(chain) or {}
        if metric not in metrics:
            continue
        day = parse_day(text)
        if start <= day <= end and (latest is None or day > latest[0]):
            latest = (day, float(metrics[metric]))
    if latest is None or latest[0] < end - timedelta(days=2):
        return None
    return latest[1]


def top5_share(days: dict[str, dict[str, dict[str, float]]], start: date, end: date, metric: str, mode: str) -> float | None:
    totals: dict[str, float] = {}
    chains = {chain for bucket in days.values() for chain in bucket}
    for chain in chains:
        if mode == "end":
            value = end_value(days, chain, start, end, metric)
            if value is None:
                continue
        else:
            rows = []
            for text, bucket in days.items():
                if chain not in bucket or metric not in bucket[chain]:
                    continue
                day = parse_day(text)
                if start <= day <= end:
                    rows.append((day, bucket[chain]))
            if len(rows) < 40:
                continue
            value = sum(float(metrics[metric]) for _, metrics in rows)
        if value > 0:
            totals[chain] = value
    if CHAIN not in totals:
        return None
    ranked = sorted(totals.values(), reverse=True)[:5]
    base = sum(ranked)
    if base <= 0:
        return None
    return totals[CHAIN] / base * 100


def quarter_row(days: dict[str, dict[str, dict[str, float]]], year: int, quarter: int) -> dict[str, Any]:
    start, end = quarter_bounds(year, quarter)
    rows = chain_days(days, CHAIN, start, end)
    tx = sum_metric(rows, "txcount")
    seconds = ((end - start).days + 1) * 86400
    daa_vals = [float(metrics["daa"]) for _, metrics in rows if "daa" in metrics]
    return {
        "year": year,
        "quarter": quarter,
        "end": end.isoformat(),
        "txcount": round(tx, 2),
        "tps": round(tx / seconds, 2) if seconds else None,
        "daa_avg": round(sum(daa_vals) / len(daa_vals), 2) if daa_vals else None,
        "fees_usd": round(sum_metric(rows, "fees_paid_usd"), 2),
        "app_fees_usd": round(sum_metric(rows, "app_fees_usd"), 2),
        "tvl_usd": end_value(days, CHAIN, start, end, "tvl"),
        "tvl_top5_pct": top5_share(days, start, end, "tvl", "end"),
        "app_fees_top5_pct": top5_share(days, start, end, "app_fees_usd", "sum"),
        "days": len(rows),
    }


def pct(curr: float | None, prev: float | None) -> float | None:
    if curr is None or prev is None or prev == 0:
        return None
    return (curr / prev - 1) * 100


def usd(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1e8:
        return f"{value / 1e8:.0f} 亿美元"
    return f"{value / 1e4:.0f} 万美元"


def tx_text(value: float) -> str:
    if value >= 1e8:
        return f"{value / 1e8:.2f} 亿笔"
    return f"{value / 1e4:.0f} 万笔"


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def complete_months(days: dict[str, dict[str, dict[str, float]]], today: date) -> list[tuple[int, int]]:
    found: set[tuple[int, int]] = set()
    for text in days:
        if CHAIN not in days[text] or "txcount" not in days[text][CHAIN]:
            continue
        day = parse_day(text)
        found.add((day.year, day.month))
    ready: list[tuple[int, int]] = []
    for year, month in sorted(found):
        start, end = month_bounds(year, month)
        if end >= today:
            continue
        rows = chain_days(days, CHAIN, start, end)
        span = (end - start).days + 1
        if len(rows) < span - 2:
            continue
        if rows[-1][0] < end - timedelta(days=2):
            continue
        ready.append((year, month))
    return ready


def month_row(days: dict[str, dict[str, dict[str, float]]], year: int, month: int) -> dict[str, Any]:
    start, end = month_bounds(year, month)
    rows = chain_days(days, CHAIN, start, end)
    tx = sum_metric(rows, "txcount")
    return {
        "year": year,
        "month": month,
        "end": end.isoformat(),
        "txcount": round(tx, 2),
        "days": len(rows),
    }


def ending_streak(rows: list[dict[str, Any]], line: float = MONTH_LINE) -> int:
    run = 0
    for row in reversed(rows):
        if float(row["txcount"]) >= line:
            run += 1
            continue
        break
    return run


def max_streak(rows: list[dict[str, Any]], line: float = MONTH_LINE) -> int:
    best = 0
    run = 0
    for row in rows:
        if float(row["txcount"]) >= line:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def build_month_news(rows: list[dict[str, Any]]) -> str | None:
    if not rows:
        return None
    latest = rows[-1]
    prior = rows[:-1]
    prior_max = max((float(row["txcount"]) for row in prior), default=0.0)
    ath = float(latest["txcount"]) > prior_max + 1e-6 if prior else False
    streak = ending_streak(rows)
    prior_best = max_streak(prior) if prior else 0
    streak_hit = streak >= 4 and streak > prior_best
    if not ath and not streak_hit:
        return None
    bits = [
        f"据吴说数据中心统计，{latest['year']} 年 {latest['month']} 月以太坊交易约 {tx_text(float(latest['txcount']))}。"
    ]
    if ath:
        if prior:
            bits.append(f"高于此前每一个完整月（此前最高约 {tx_text(prior_max)}）。")
        else:
            bits.append("这是本地已存完整月里的最高值。")
    if streak_hit:
        bits.append(f"已连续 {streak} 个完整月交易超过 6000 万笔，创下本地已存序列最长纪录。")
    over = sum(1 for row in rows if float(row["txcount"]) >= MONTH_LINE)
    bits.append(f"已存完整月共 {len(rows)} 个，其中 {over} 个超过 6000 万笔。")
    return "".join(bits) + f"\n{PAGE}"


def build_news(row: dict[str, Any], prior: dict[str, Any] | None, yoy: dict[str, Any] | None, stored: list[dict[str, Any]]) -> str:
    bits = [
        f"据吴说数据中心统计，{row['year']} 年第 {row['quarter']} 季度以太坊交易约 {tx_text(row['txcount'])}，"
        f"折合 TPS 约 {row['tps']:.2f}。"
    ]
    if row.get("daa_avg"):
        bits.append(f"日均活跃地址约 {row['daa_avg'] / 1e4:.0f} 万。")
    bits.append(f"链上手续费约 {usd(row['fees_usd'])}，应用手续费约 {usd(row['app_fees_usd'])}。")
    if row.get("tvl_usd"):
        bits.append(f"季末锁仓约 {usd(row['tvl_usd'])}")
        if row.get("tvl_top5_pct") is not None:
            bits[-1] += f"，约占这份链样本前五名的 {row['tvl_top5_pct']:.1f}%"
        bits[-1] += "。"
    if row.get("app_fees_top5_pct") is not None:
        bits.append(f"应用手续费约占这份链样本前五名的 {row['app_fees_top5_pct']:.1f}%。")
    prior_tx = pct(row["txcount"], prior["txcount"]) if prior else None
    yoy_tx = pct(row["txcount"], yoy["txcount"]) if yoy else None
    extra = []
    if prior_tx is not None:
        extra.append(f"交易笔数环比 {prior_tx:+.1f}%")
    if yoy_tx is not None:
        extra.append(f"同比 {yoy_tx:+.1f}%")
    earlier = [item["txcount"] for item in stored if (item["year"], item["quarter"]) < (row["year"], row["quarter"])]
    if earlier and row["txcount"] > max(earlier):
        extra.append("交易笔数为已存季度新高")
    earlier_fees = [item["fees_usd"] for item in stored if (item["year"], item["quarter"]) < (row["year"], row["quarter"]) and item["fees_usd"] > 0]
    if earlier_fees and row["fees_usd"] < min(earlier_fees):
        extra.append("链上手续费为已存季度新低")
    if extra:
        bits.append("，".join(extra) + "。")
    return "".join(bits) + f"\n{PAGE}"


def push_brief(state: dict[str, Any], msg_id: str, text: str, do_push: bool, force: bool) -> int:
    print("---")
    print(f"id={msg_id}")
    print(as_news(text))
    if msg_id in state["sentIds"] and not force:
        print(f"[skip] already {msg_id}")
        return 0
    if not do_push:
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
    args = parser.parse_args()
    days = load_days()
    try:
        added = merge_remote(days)
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] 拉数失败: {exc}", file=sys.stderr)
        return 1
    today = datetime.now(timezone.utc).date()
    ready = complete_quarters(days, today)
    months = complete_months(days, today)
    latest = quarter_row(days, *ready[-1]) if ready else None
    month_rows = [month_row(days, y, m) for y, m in months]
    latest_month = month_rows[-1] if month_rows else None
    save_doc(days, latest, latest_month)
    eth_days = sum(1 for bucket in days.values() if CHAIN in bucket and "txcount" in bucket[CHAIN])
    print(f"[eth-quarter] stored_days={eth_days} remote_updates={added} complete={ready or 'none'}")
    print(
        f"[eth-month] complete={months[-3:] if months else 'none'} "
        f"latest_tx={latest_month['txcount'] if latest_month else 'none'}"
    )

    state = load_state()
    code = 0

    month_text = build_month_news(month_rows) if month_rows else None
    if latest_month is None:
        print("[skip] 还没有收齐的完整月。当前窗口会继续累积。")
    else:
        _, end = month_bounds(int(latest_month["year"]), int(latest_month["month"]))
        age = (today - end).days
        print(f"[eth-month] latest={latest_month['year']}-{latest_month['month']:02d} age={age}d")
        if age > MONTH_FRESH_DAYS and not args.force:
            print(f"[skip] 月结束已 {age} 天，超过 {MONTH_FRESH_DAYS} 天，不补历史月")
        elif not month_text:
            print("[skip] 最新完整月未创笔数新高，也未创连续≥4 个月超 6000 万纪录")
        else:
            msg_id = f"eth-month-tx-{latest_month['year']}-{int(latest_month['month']):02d}"
            month_code = push_brief(state, msg_id, month_text, args.push, args.force)
            code = code or month_code
            state = load_state()

    if not ready:
        print("[skip] 还没有收齐的季度。当前窗口会继续累积，下一季结束再发。")
        return code
    year, quarter = ready[-1]
    _, end = quarter_bounds(year, quarter)
    age = (today - end).days
    print(f"[eth-quarter] latest={year}Q{quarter} age={age}d tx={latest['txcount']:.0f} tps={latest['tps']}")
    if age > FRESH_DAYS and not args.force:
        print(f"[skip] 季结束已 {age} 天，超过 {FRESH_DAYS} 天，不补历史")
        return code
    stored = [quarter_row(days, y, q) for y, q in ready]
    prior = stored[-2] if len(stored) >= 2 else None
    yoy = next((item for item in stored if item["year"] == year - 1 and item["quarter"] == quarter), None)
    text = build_news(latest, prior, yoy, stored)
    msg_id = f"eth-quarter-{year}Q{quarter}"
    return code or push_brief(state, msg_id, text, args.push, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
