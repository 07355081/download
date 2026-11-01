#!/usr/bin/env python3
"""比特币网络活动（免费近邻）→ Lark 新闻体。

只发最新：日交易笔数最新完整日（≤3 天）+ 当前内存池快照。
推送只附看板页 /dashboard/btc-network，不附 JSON。
- 日常：日笔数、相对序列高点、内存池待处理笔数
- 阈值：日笔数刚越 80 万，或 |日环比|≥15%，或内存池≥10 万且创近 180 日采样新高（用当日快照相对历史文件内峰值近似）

不写微交易占比、OP_RETURN。不回扫外电旧窗口。

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
    HERE / "output" / "json" / "btc_network.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "btc-network" / "btc_network.json",
    Path("/root/dashboard/public/json/btc-network/btc_network.json"),
]
STATE_PATH = HERE / "output" / "btc_network_lark.json"
MEMPOOL_PEAK_PATH = HERE / "output" / "mempool_peak.json"
LOG_PATH = HERE / "flash.log"
PAGE_URL = "https://data.wublock123.com/dashboard/btc-network"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
MARK_DAILY = "【实时日常报告】"
MARK_THRESHOLD = "【历史阈值警报】"
FRESH_DAYS = 3
TX_LINE = 800_000
DOD_PCT = 15.0
MEMPOOL_LINE = 100_000

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
                print(f"[btc-network] json={path}")
                return doc
    raise FileNotFoundError("找不到 btc_network.json，请先跑 download.py")


def load_mempool_peak() -> dict[str, Any]:
    if not MEMPOOL_PEAK_PATH.is_file():
        return {"peak": 0, "peak_at": None}
    try:
        doc = json.loads(MEMPOOL_PEAK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"peak": 0, "peak_at": None}
    peak = doc.get("peak") if isinstance(doc, dict) else None
    return {
        "peak": int(peak) if isinstance(peak, (int, float)) else 0,
        "peak_at": doc.get("peak_at") if isinstance(doc, dict) else None,
    }


def save_mempool_peak(count: int, asof: str) -> None:
    prev = load_mempool_peak()
    peak = max(int(prev["peak"] or 0), count)
    peak_at = asof if count >= peak else prev.get("peak_at")
    MEMPOOL_PEAK_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"peak": peak, "peak_at": peak_at, "updatedAt": now_iso()}
    tmp = MEMPOOL_PEAK_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(MEMPOOL_PEAK_PATH)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cn_day(day: str) -> str:
    y, m, d = day.split("-")
    return f"{int(y)} 年 {int(m)} 月 {int(d)} 日"


def cn_clock(iso: str) -> str:
    text = str(iso or "").strip()
    if not text:
        local = datetime.now(TZ_CN)
        return f"{local.year} 年 {local.month} 月 {local.day} 日 {local.strftime('%H:%M')}"
    normalized = text.replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(normalized)
    except ValueError:
        return text
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(TZ_CN)
    return f"{local.year} 年 {local.month} 月 {local.day} 日 {local.strftime('%H:%M')}"


def fmt_tx(n: float) -> str:
    if n >= 1e4:
        return f"{n / 1e4:.1f} 万笔"
    return f"{int(n)} 笔"


def pct(n: float) -> str:
    sign = "+" if n > 0 else ""
    return f"{sign}{n:.1f}%"


def fresh(day: str) -> bool:
    d = datetime.strptime(day, "%Y-%m-%d").date()
    return (datetime.now(timezone.utc).date() - d).days <= FRESH_DAYS


def daily_text(latest: dict[str, Any], mempool: dict[str, Any]) -> str:
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
    vs = latest.get("vs_peak_pct")
    vs_bit = ""
    if isinstance(vs, (int, float)) and latest.get("peak_day"):
        vs_bit = (
            f"相对本序列高点（{latest['peak_day']} 约 {fmt_tx(float(latest['peak_tx_count']))}）"
            f"约 {pct(float(vs))}。"
        )
    return (
        f"据吴说数据中心统计，截至 {cn_day(str(latest['day']))}，"
        f"比特币网络日交易约 {fmt_tx(float(latest['tx_count']))}。"
        f"{vs_bit}"
        f"另据截至 {stamp}（UTC+8）的内存池快照，待处理交易约 {fmt_tx(float(mempool['count']))}。"
        f"口径为 CoinMetrics 日交易笔数与 mempool.space 瞬时计数，"
        f"不含微交易占比或 OP_RETURN 拆分。"
        f"\n{PAGE_URL}"
    )


def threshold_reasons(
    latest: dict[str, Any],
    prev: dict[str, Any] | None,
    mempool: dict[str, Any],
    prev_peak: int,
) -> list[str]:
    reasons: list[str] = []
    tx = float(latest["tx_count"])
    if prev is not None:
        prev_tx = float(prev["tx_count"])
        if prev_tx < TX_LINE <= tx:
            reasons.append(f"日交易约 {fmt_tx(tx)}，刚越过 {fmt_tx(TX_LINE)}")
    dod = latest.get("dod_pct")
    if isinstance(dod, (int, float)) and abs(float(dod)) >= DOD_PCT:
        reasons.append(f"日交易环比约 {pct(float(dod))}")
    count = int(mempool["count"])
    if count >= MEMPOOL_LINE and count > prev_peak:
        reasons.append(f"内存池待处理约 {fmt_tx(count)}，创本管道采样新高")
    return reasons


def recent_low(rows: list[dict[str, Any]], day: str, window: int = 7) -> dict[str, Any] | None:
    picked = [row for row in rows if str(row.get("day") or "") <= day and isinstance(row.get("tx_count"), (int, float))]
    picked = picked[-window:]
    if len(picked) < 3:
        return None
    return min(picked, key=lambda row: float(row["tx_count"]))


def threshold_text(
    latest: dict[str, Any],
    prev: dict[str, Any] | None,
    rows: list[dict[str, Any]],
    mempool: dict[str, Any],
) -> str:
    day = str(latest["day"])
    tx = float(latest["tx_count"])
    opening = f"吴说获悉，据吴说数据中心统计，{cn_day(day)}比特币网络日交易约 {fmt_tx(tx)}"
    if prev is not None and isinstance(prev.get("tx_count"), (int, float)):
        prev_tx = float(prev["tx_count"])
        delta = tx - prev_tx
        verb = "增加" if delta >= 0 else "减少"
        opening += f"，较 {cn_day(str(prev['day']))}的 {fmt_tx(prev_tx)}{verb}约 {fmt_tx(abs(delta))}"
        dod = latest.get("dod_pct")
        if isinstance(dod, (int, float)):
            move = "上升" if float(dod) >= 0 else "下降"
            opening += f"，环比{move} {abs(float(dod)):.1f}%"
    sentences = [opening + "。"]

    if prev is not None and isinstance(prev.get("tx_count"), (int, float)):
        prev_tx = float(prev["tx_count"])
        if prev_tx < TX_LINE <= tx:
            sentences.append("日交易笔数重新站上 80 万笔。")
        elif prev_tx >= TX_LINE > tx:
            sentences.append("日交易笔数回落到 80 万笔以下。")

    low = recent_low(rows, day)
    if low is not None and str(low.get("day")) != day:
        low_tx = float(low["tx_count"])
        if low_tx > 0:
            rebound = (tx / low_tx - 1.0) * 100.0
            sentences.append(
                f"近 7 个完整日里，低点出现在 {cn_day(str(low['day']))}，约 {fmt_tx(low_tx)}，"
                f"{cn_day(day)}较该低点回升 {rebound:.1f}%。"
            )

    vs = latest.get("vs_peak_pct")
    peak_day = latest.get("peak_day")
    peak_tx = latest.get("peak_tx_count")
    if isinstance(vs, (int, float)) and peak_day and isinstance(peak_tx, (int, float)):
        if abs(float(vs)) < 0.05:
            sentences.append(f"这一水平即 2023 年以来本序列高点。")
        elif float(vs) < 0:
            sentences.append(
                f"这一水平仍低于 2023 年以来本序列高点，"
                f"{cn_day(str(peak_day))}约 {fmt_tx(float(peak_tx))}，低 {abs(float(vs)):.1f}%。"
            )

    count = mempool.get("count")
    if isinstance(count, (int, float)) and float(count) > 0:
        when = cn_clock(str(mempool.get("asof") or ""))
        backlog = f"另据 {when}（UTC+8）的内存池快照，待处理交易约 {fmt_tx(float(count))}"
        if float(count) >= MEMPOOL_LINE:
            backlog += "，高于 10 万笔"
        sentences.append(backlog + "。")

    sentences.append("日交易笔数为 CoinMetrics 的 TxCnt，内存池为当时快照，不含微交易占比。")
    return "".join(sentences) + f"\n{PAGE_URL}"


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
    mempool = doc.get("mempool") or {}
    day = str(latest["day"])
    if not fresh(day):
        print(f"[skip] 最新日 {day} 超过 {FRESH_DAYS} 天")
        return 0
    rows = [r for r in (doc.get("data") or []) if isinstance(r, dict)]
    prev = rows[-2] if len(rows) >= 2 else None
    peak_state = load_mempool_peak()
    prev_peak = int(peak_state["peak"] or 0)
    count = int(mempool.get("count") or 0)
    asof = str(mempool.get("asof") or now_iso())

    briefs: list[dict[str, str]] = []
    if not args.no_daily:
        briefs.append({"id": f"btc-network-daily-{day}", "text": f"{MARK_DAILY}\n{daily_text(latest, mempool)}"})
    reasons = threshold_reasons(latest, prev, mempool, prev_peak)
    if reasons:
        briefs.append(
            {
                "id": f"btc-network-threshold-{day}",
                "text": f"{MARK_THRESHOLD}\n{threshold_text(latest, prev, rows, mempool)}",
            }
        )
    else:
        print("[info] 未过阈值")

    # update peak after evaluating (so first time over line can fire)
    if count > 0:
        save_mempool_peak(count, asof)

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
