#!/usr/bin/env python3
"""x402 日交易（免费 x402stats）→ Lark 新闻体。

只发最新：
- 日常：最新完整日笔数、成交额、均单笔（≤3 天）
- 阈值：笔数 |日环比|≥40%，或成交额 |日环比|≥50%，或均单笔刚越 $0.15
- 月度（历史级）：最新已收整月，且月末后 ≤14 天；发月均日笔数/成交/均单笔；不回扫旧月

非 Artemis；无 BlockRun。

用法：
  python flash.py
  python flash.py --push
"""
from __future__ import annotations

import argparse
import base64
import calendar
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    HERE / "output" / "json" / "x402_stats.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "x402" / "x402_stats.json",
    Path("/root/dashboard/public/json/x402/x402_stats.json"),
]
STATE_PATH = HERE / "output" / "x402_lark.json"
LOG_PATH = HERE / "flash.log"
# 看板目录页（去中心化 → x402）；静态图作兜底
PAGE = "https://data.wublock123.com/dashboard/x402"
CHART_URL = "https://data.wublock123.com/dashboard/x402"
JSON_URL = PAGE
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
JSON_URL = "https://data.wublock123.com/dashboard/x402"
MARK_DAILY = "【实时日常报告】"
MARK_THRESHOLD = "【历史阈值警报】"
FRESH_DAYS = 3
TX_DOD = 40.0
VOL_DOD = 50.0
AVG_LINE = 0.15
MONTH_WINDOW_DAYS = 14

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
    body = as_news(text)
    for mark in (MARK_THRESHOLD, MARK_DAILY):
        if text.startswith(mark):
            body = f"{mark}\n{body}"
            break
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": body}}
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
        raw = resp.read().decode("utf-8", errors="replace")
    if raw:
        result = json.loads(raw)
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
            if isinstance(doc, dict) and isinstance(doc.get("latest_day"), dict):
                print(f"[x402] json={path}")
                return doc
    raise FileNotFoundError("找不到 x402_stats.json，请先跑 download.py")


def cn_day(day: str) -> str:
    y, m, d = day.split("-")
    return f"{int(y)} 年 {int(m)} 月 {int(d)} 日"


def cn_month(ym: str) -> str:
    y, m = ym.split("-")
    return f"{int(y)} 年 {int(m)} 月"


def fmt_txs(n: float) -> str:
    if n >= 1e4:
        return f"{n / 1e4:.1f} 万笔"
    return f"{int(n)} 笔"


def fmt_usd(n: float) -> str:
    if abs(n) >= 1e6:
        return f"{n / 1e6:.2f} 百万美元"
    if abs(n) >= 1e4:
        return f"{n / 1e4:.1f} 万美元"
    return f"{n:.2f} 美元"


def pct(n: float) -> str:
    sign = "+" if n > 0 else ""
    return f"{sign}{n:.1f}%"


def fresh(day: str) -> bool:
    d = datetime.strptime(day, "%Y-%m-%d").date()
    return (datetime.now(timezone.utc).date() - d).days <= FRESH_DAYS


def daily_text(row: dict[str, Any]) -> str:
    avg = row.get("avg_usd")
    avg_bit = f"均单笔约 {float(avg):.2f} 美元。" if isinstance(avg, (int, float)) else ""
    dod = row.get("txs_dod_pct")
    dod_bit = f"笔数日环比约 {pct(float(dod))}。" if isinstance(dod, (int, float)) else ""
    return (
        f"据吴说数据中心统计，截至 {cn_day(str(row['day']))}，"
        f"x402 日交易约 {fmt_txs(float(row['transactions']))}，"
        f"成交额约 {fmt_usd(float(row['volume_usd']))}。"
        f"{avg_bit}{dod_bit}"
        f"口径为 x402stats 日序列，非 Artemis；不含 BlockRun 等卖家拆分。"
        f"\n{PAGE}"
    )


def threshold_reasons(row: dict[str, Any], prev: dict[str, Any] | None) -> list[str]:
    reasons: list[str] = []
    td = row.get("txs_dod_pct")
    if isinstance(td, (int, float)) and abs(float(td)) >= TX_DOD:
        reasons.append(f"日交易笔数环比约 {pct(float(td))}")
    vd = row.get("vol_dod_pct")
    if isinstance(vd, (int, float)) and abs(float(vd)) >= VOL_DOD:
        reasons.append(f"日成交额环比约 {pct(float(vd))}")
    avg = row.get("avg_usd")
    if prev is not None and isinstance(avg, (int, float)) and isinstance(prev.get("avg_usd"), (int, float)):
        prev_avg = float(prev["avg_usd"])
        cur = float(avg)
        if (prev_avg > AVG_LINE >= cur) or (prev_avg < AVG_LINE <= cur):
            reasons.append(f"均单笔约 {cur:.2f} 美元，刚越过 {AVG_LINE:.2f} 美元")
    return reasons


def threshold_text(row: dict[str, Any], reasons: list[str]) -> str:
    avg = row.get("avg_usd")
    avg_bit = f"均单笔约 {float(avg):.2f} 美元。" if isinstance(avg, (int, float)) else ""
    return (
        f"据吴说数据中心统计，x402 日交易出现明显变化：{'；'.join(reasons)}。"
        f"截至 {cn_day(str(row['day']))} 约 {fmt_txs(float(row['transactions']))}，"
        f"成交额约 {fmt_usd(float(row['volume_usd']))}。{avg_bit}"
        f"口径为 x402stats，非 Artemis。"
        f"\n{PAGE}"
    )


def latest_complete_month(rows: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]] | None:
    """Return (YYYY-MM, days) for the newest fully closed month present in data."""
    today = datetime.now(timezone.utc).date()
    by_ym: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        ym = str(row["day"])[:7]
        by_ym.setdefault(ym, []).append(row)
    for ym in sorted(by_ym.keys(), reverse=True):
        y, m = map(int, ym.split("-"))
        last_day = date(y, m, calendar.monthrange(y, m)[1])
        if last_day >= today:
            continue  # month not closed
        days_since = (today - last_day).days
        if days_since > MONTH_WINDOW_DAYS:
            continue
        days = by_ym[ym]
        # require nearly full month (>=28 or = month length)
        need = calendar.monthrange(y, m)[1]
        if len(days) < min(28, need - 1):
            continue
        return ym, days
    return None


def month_text(ym: str, days: list[dict[str, Any]]) -> str:
    n = len(days)
    txs = sum(int(d["transactions"]) for d in days)
    vol = sum(float(d["volume_usd"]) for d in days)
    avg_daily = txs / n if n else 0
    avg_size = vol / txs if txs else 0
    return (
        f"据吴说数据中心统计，{cn_month(ym)} x402 共约 {fmt_txs(float(txs))}（{n} 个完整日），"
        f"日均约 {fmt_txs(avg_daily)}，成交额合计约 {fmt_usd(vol)}，"
        f"均单笔约 {avg_size:.2f} 美元。"
        f"口径为 x402stats 月汇总，非 Artemis；不含 BlockRun 拆分。不回扫更早月份。"
        f"\n{PAGE}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-daily", action="store_true")
    args = parser.parse_args()
    try:
        doc = load_doc()
    except (OSError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    latest = doc["latest_day"]
    rows = [r for r in (doc.get("data") or []) if isinstance(r, dict) and r.get("day")]
    rows.sort(key=lambda r: str(r["day"]))
    day = str(latest["day"])
    briefs: list[dict[str, str]] = []

    if fresh(day):
        prev = rows[-2] if len(rows) >= 2 else None
        if not args.no_daily:
            briefs.append({"id": f"x402-daily-{day}", "text": f"{MARK_DAILY}\n{daily_text(latest)}"})
        reasons = threshold_reasons(latest, prev)
        if reasons:
            briefs.append(
                {
                    "id": f"x402-threshold-{day}",
                    "text": f"{MARK_THRESHOLD}\n{threshold_text(latest, reasons)}",
                }
            )
        else:
            print("[info] 日阈值未过线")
    else:
        print(f"[skip] 最新日 {day} 超过 {FRESH_DAYS} 天")

    month = latest_complete_month(rows)
    if month:
        ym, days = month
        briefs.append(
            {
                "id": f"x402-month-{ym}",
                "text": f"{MARK_THRESHOLD}\n{month_text(ym, days)}",
            }
        )
    else:
        print("[info] 无在窗口内的已收整月")

    state = load_state()
    for brief in briefs:
        print("---")
        print(f"id={brief['id']} already={brief['id'] in state['sentIds']}")
        print(as_news(brief["text"]))
    if not briefs:
        return 0
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0

    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    sent_any = False
    for brief in briefs:
        if brief["id"] in state["sentIds"] and not args.force:
            print(f"[skip] already {brief['id']}")
            continue
        try:
            send_lark(webhook, secret, brief["text"])
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] Lark 失败 {brief['id']}: {exc}", file=sys.stderr)
            append_log(stamp, brief["id"], False, brief["text"])
            return 1
        print(f"[pushed] {brief['id']}")
        if brief["id"] not in state["sentIds"]:
            state["sentIds"].append(brief["id"])
        sent_any = True
        append_log(stamp, brief["id"], True, brief["text"])
        time.sleep(0.4)
    if sent_any:
        state["sentIds"] = state["sentIds"][-200:]
        save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
