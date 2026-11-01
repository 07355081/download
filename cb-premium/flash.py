# -*- coding: utf-8 -*-
"""Coinbase 比特币溢价指数 → Lark。

- 日常：最近一个已结束 UTC 日【实时日常报告】
- 阈值：当前连续负溢价天数创已存序列新高 → 【历史阈值警报】+【特殊警报】
- --history-scan：回扫近期曾创纪录且未推送的结束日

用法：
  python flash.py
  python flash.py --push --history-scan
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    HERE / "output" / "json" / "cb_premium.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "cb-premium" / "cb_premium.json",
    Path("/root/dashboard/public/json/cb-premium/cb_premium.json"),
]
STATE_PATH = HERE / "output" / "cb_premium_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
PAGE = "https://data.wublock123.com/dashboard/btc-indicators"
LARK_DAILY = "【实时日常报告】"
LARK_THRESHOLD = "【历史阈值警报】"
LARK_SPECIAL = "【特殊警报】"
HISTORY_DAYS = 60
HISTORY_LIMIT = 4


def _ensure_lark_copy() -> None:
    import sys as _sys
    from pathlib import Path as _Path
    folder = _Path(__file__).resolve().parent
    while folder != folder.parent and not (folder / "lark_copy.py").is_file():
        folder = folder.parent
    if str(folder) not in _sys.path:
        _sys.path.insert(0, str(folder))


_ensure_lark_copy()
from lark_copy import as_news

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
    return env


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def save_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def resolve_json() -> Path:
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 cb_premium.json")


def fmt_rate(n: float) -> str:
    return f"{n:+.4f}%"


def closed_days(doc: dict[str, Any]) -> list[dict[str, Any]]:
    today = datetime.now(timezone.utc).date().isoformat()
    rows = [r for r in (doc.get("days") or []) if isinstance(r, dict) and r.get("day") and r["day"] < today]
    return sorted(rows, key=lambda r: r["day"])


def streak_as_of(days: list[dict[str, Any]], end_day: str) -> tuple[int, str | None, int, bool]:
    """截至 end_day（含）的连续负溢价天数、起点、此前最长已结束负溢价、是否创新高。"""
    subset = [r for r in days if r["day"] <= end_day]
    if not subset:
        return 0, None, 0, False
    streak = 0
    start = None
    for row in reversed(subset):
        if float(row["premium_rate"]) < 0:
            streak += 1
            start = row["day"]
        else:
            break
    # prior completed streaks before current one
    prior_max = 0
    run = 0
    for row in subset:
        if float(row["premium_rate"]) < 0:
            run += 1
        else:
            if run:
                # if this run ends before current streak start, count it
                prior_max = max(prior_max, run)
            run = 0
    if streak > 0 and start:
        # recompute prior_max excluding the ongoing streak that includes end_day
        prior_max = 0
        run = 0
        run_start = None
        for row in subset:
            if float(row["premium_rate"]) < 0:
                if run == 0:
                    run_start = row["day"]
                run += 1
            else:
                if run and run_start and run_start < start:
                    prior_max = max(prior_max, run)
                run = 0
                run_start = None
        if run and run_start and run_start < start:
            prior_max = max(prior_max, run)
    else:
        prior_max = 0
        run = 0
        for row in subset:
            if float(row["premium_rate"]) < 0:
                run += 1
            else:
                prior_max = max(prior_max, run)
                run = 0
        prior_max = max(prior_max, run)
    is_record = streak > 0 and streak > prior_max
    return streak, start, prior_max, is_record


def build_daily(row: dict[str, Any], streak: int, start: str | None, prior_max: int, is_record: bool) -> str:
    title = f"Coinbase 比特币溢价 {fmt_rate(float(row['premium_rate']))}"
    if streak > 0:
        title += f"，连续负溢价 {streak} 天"
    body = (
        f"据吴说数据中心统计，{row['day']}（UTC）Coinbase 比特币溢价指数报 {fmt_rate(float(row['premium_rate']))}"
        f"（溢价额约 {float(row.get('premium') or 0):+.2f} 美元）。"
    )
    if streak > 0 and start:
        body += f"自 {start} 起已连续负溢价 {streak} 天"
        if is_record:
            body += f"，高于此前已结束最长纪录 {prior_max} 天，续创本序列新高"
        else:
            body += f"；此前已结束最长纪录为 {prior_max} 天"
        body += "。"
    else:
        body += "当日不在连续负溢价区间。"
    body += f"数据来自 CoinGlass 日线。\n{PAGE}"
    return f"{title}\n{body}"


def build_threshold(
    row: dict[str, Any],
    streak: int,
    start: str | None,
    prior_max: int,
    *,
    historical: bool,
) -> str:
    body = (
        f"【特殊】Coinbase 比特币溢价连续负溢价创新高：{streak} 天\n"
        f"吴说获悉，据吴说数据中心统计，截至 {row['day']}（UTC），Coinbase 比特币溢价指数"
        f"自 {start} 起连续负溢价 {streak} 天，最新报 {fmt_rate(float(row['premium_rate']))}，"
        f"超过此前已结束最长纪录 {prior_max} 天。本条不是每日例行稿。"
    )
    if historical:
        found = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
        body += f"本条为历史回扫发现（交易日 {row['day']}，发现时间 {found} UTC+8），请勿当成刚收盘的一天。"
    body += f"\n{PAGE}"
    return body


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(
            hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
        ).decode()
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    if raw and str(json.loads(raw).get("code", "0")) != "0":
        raise RuntimeError(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--history-scan", action="store_true")
    parser.add_argument("--no-daily", action="store_true")
    args = parser.parse_args()
    if getattr(args, "history_scan", False):
        args.history_scan = False
    try:
        doc = load_json(resolve_json())
        days = closed_days(doc)
        if len(days) < 30:
            raise RuntimeError("已结束日不足")
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"[ERROR] {exc}")
        return 1

    latest = days[-1]
    streak, start, prior_max, is_record = streak_as_of(days, latest["day"])
    print(
        f"[cb-premium] latest={latest['day']} rate={latest['premium_rate']} "
        f"streak={streak} prior_max={prior_max} record={is_record}",
        flush=True,
    )
    briefs: list[dict[str, Any]] = []
    if is_record:
        briefs.append({
            "id": f"cb-premium-record-{latest['day']}",
            "kind": "threshold",
            "text": build_threshold(latest, streak, start, prior_max, historical=False),
        })
    if not args.no_daily:
        briefs.append({
            "id": f"cb-premium-daily-{latest['day']}",
            "kind": "daily",
            "text": build_daily(latest, streak, start, prior_max, is_record),
        })

    state = load_json(STATE_PATH)
    sent = {x for x in state.get("sentIds", []) if isinstance(x, str)}
    if args.history_scan:
        n = 0
        for row in reversed(days[-HISTORY_DAYS:]):
            if row["day"] == latest["day"] or n >= HISTORY_LIMIT:
                continue
            s, st, pm, rec = streak_as_of(days, row["day"])
            if not rec:
                continue
            msg_id = f"cb-premium-record-{row['day']}"
            if msg_id in sent and not args.force:
                continue
            briefs.append({
                "id": msg_id,
                "kind": "threshold",
                "text": build_threshold(row, s, st, pm, historical=True),
            })
            n += 1

    seen: set[str] = set()
    unique = []
    for brief in sorted(briefs, key=lambda item: (0 if item["kind"] == "threshold" else 1, item["id"])):
        if brief["id"] in seen:
            continue
        seen.add(brief["id"])
        unique.append(brief)
    for brief in unique:
        mark = f"{LARK_THRESHOLD}\n{LARK_SPECIAL}" if brief["kind"] == "threshold" else LARK_DAILY
        print("---")
        print(brief["id"])
        print(f"{mark}\n{brief['text']}")
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0
    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK")
        return 2
    sent_ids = [x for x in state.get("sentIds", []) if isinstance(x, str)]
    sent_any = False
    for brief in unique:
        if brief["id"] in sent_ids and not args.force:
            print("[skip]", brief["id"])
            continue
        mark = f"{LARK_THRESHOLD}\n{LARK_SPECIAL}" if brief["kind"] == "threshold" else LARK_DAILY
        try:
            send_lark(webhook, secret, f"{mark}\n{brief['text']}")
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] {brief['id']}: {exc}")
            return 1
        print("[pushed]", brief["id"])
        sent_ids.append(brief["id"])
        sent_any = True
        time.sleep(0.4)
    if sent_any:
        save_json(
            STATE_PATH,
            {
                "sentIds": sent_ids[-200:],
                "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
