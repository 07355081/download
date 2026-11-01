# -*- coding: utf-8 -*-
"""Coinbase Base 股票代币 DEX 成交 → Lark。

口径：Dune dex.trades，已知 0xb200 合约，一笔兑换计一次。不是 Token Terminal 原表。

- 日常：最近一个已结束 UTC 日【实时日常报告】，附带近 30 日合计
- 阈值：|较前一日| ≥ 40% → 【历史阈值警报】+【特殊警报】
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
JSON_PATH = HERE / "output" / "json" / "cb_stock_dex.json"
STATE_PATH = HERE / "output" / "cb_stock_dex_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/cb-stock-dex"
MAJOR_PCT = 40.0
HISTORY_DAYS = 40
HISTORY_LIMIT = 4
LARK_DAILY = "【实时日常报告】"
LARK_THRESHOLD = "【历史阈值警报】"
LARK_SPECIAL = "【特殊警报】"


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


def venue(project: object, version: object) -> str:
    name = str(project or "")
    ver = str(version or "")
    if name == "aerodrome":
        return "Aerodrome"
    if name == "uniswap" and ver == "4":
        return "Uniswap v4"
    return "其他"


def days_of() -> dict[str, dict[str, float]]:
    doc = load_json(JSON_PATH)
    rows = doc.get("data") if isinstance(doc.get("data"), list) else []
    out: dict[str, dict[str, float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = str(row.get("day") or "")[:10]
        if len(day) != 10:
            continue
        try:
            vol = float(row.get("volume_usd") or 0)
        except (TypeError, ValueError):
            continue
        bucket = out.setdefault(day, {"total": 0.0, "Aerodrome": 0.0, "Uniswap v4": 0.0, "其他": 0.0})
        bucket["total"] += vol
        bucket[venue(row.get("project"), row.get("version"))] += vol
    return out


def fmt_yi(n: float) -> str:
    return f"{n / 1e8:.2f} 亿美元"


def fmt_pct(n: float) -> str:
    return f"{'+' if n > 0 else ''}{n:.1f}%"


def view_of(days: dict[str, dict[str, float]], ordered: list[str], day: str) -> dict[str, Any]:
    idx = ordered.index(day)
    prev = ordered[idx - 1] if idx else None
    row = days[day]
    total = row["total"]
    dod = None
    if prev and days[prev]["total"] > 0:
        dod = (total / days[prev]["total"] - 1) * 100
    start = (datetime.fromisoformat(day) - timedelta(days=29)).date().isoformat()
    trail = [days[d]["total"] for d in ordered if start <= d <= day]
    trail_aero = [days[d]["Aerodrome"] for d in ordered if start <= d <= day]
    trail_uni = [days[d]["Uniswap v4"] for d in ordered if start <= d <= day]
    sum30 = sum(trail)
    aero30 = sum(trail_aero)
    uni30 = sum(trail_uni)
    peak = max(trail) if trail else 0.0
    return {
        "day": day,
        "total": total,
        "prev": prev,
        "dod": dod,
        "aero": row["Aerodrome"],
        "uni": row["Uniswap v4"],
        "sum30": sum30,
        "aero30": aero30,
        "uni30": uni30,
        "aero_share": aero30 / sum30 * 100 if sum30 else 0.0,
        "peak30": peak,
    }


def build_daily(view: dict[str, Any]) -> str:
    dod = view["dod"]
    dod_s = f"，较前一日（{view['prev']}）{fmt_pct(float(dod))}" if isinstance(dod, float) else ""
    title = f"Coinbase 股票代币 Base DEX 日成交 {fmt_yi(view['total'])}"
    if isinstance(dod, float):
        title += f"，环比 {fmt_pct(dod)}"
    body = (
        f"据吴说数据中心统计，{view['day']}（UTC）Coinbase 在 Base 上发行的股票代币 DEX 成交 "
        f"{fmt_yi(view['total'])}{dod_s}。其中 Aerodrome {fmt_yi(view['aero'])}，Uniswap v4 {fmt_yi(view['uni'])}。"
        f"近 30 日合计 {fmt_yi(view['sum30'])}，Aerodrome 约占 {view['aero_share']:.1f}%，"
        f"Uniswap v4 {fmt_yi(view['uni30'])}，单日最高 {fmt_yi(view['peak30'])}。"
        "口径是 Dune 上这些已知合约的兑换额，一笔只计一次，不是 Token Terminal 原表。"
        f"\n{PAGE}"
    )
    return f"{title}\n{body}"


def build_threshold(view: dict[str, Any], *, historical: bool) -> str:
    dod = float(view["dod"])
    body = (
        f"【特殊】Coinbase 股票代币 DEX 成交异动：{view['day']} 环比 {fmt_pct(dod)}\n"
        f"吴说获悉，据吴说数据中心统计，{view['day']}（UTC）这类代币在 Base 上的 DEX 成交 "
        f"{fmt_yi(view['total'])}，较前一日（{view['prev']}）{fmt_pct(dod)}，绝对值达到 {MAJOR_PCT:g}%。"
        f"当日 Aerodrome {fmt_yi(view['aero'])}，Uniswap v4 {fmt_yi(view['uni'])}。本条不是每日例行稿。"
    )
    if historical:
        found = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
        body += f"本条为历史回扫发现（交易日 {view['day']}，发现时间 {found} UTC+8），请勿当成刚收盘的一天。"
    body += f"\n{PAGE}"
    return body


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()).decode()
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
    days = days_of()
    ordered = sorted(day for day, row in days.items() if row["total"] > 0)
    if len(ordered) < 2:
        print("[ERROR] 可用日期不足")
        return 1
    today = datetime.now(timezone.utc).date().isoformat()
    ordered = [day for day in ordered if day < today]
    if len(ordered) < 2:
        print("[ERROR] 已结束日不足")
        return 1
    latest = view_of(days, ordered, ordered[-1])
    print(
        f"[cb-stock-dex] latest={latest['day']} total={latest['total']:.0f} "
        f"dod={latest['dod']} sum30={latest['sum30']:.0f}",
        flush=True,
    )
    briefs: list[dict[str, Any]] = []
    if isinstance(latest["dod"], float) and abs(latest["dod"]) >= MAJOR_PCT:
        briefs.append({"id": f"cb-stock-dex-threshold-{latest['day']}", "kind": "threshold", "text": build_threshold(latest, historical=False)})
    if not args.no_daily:
        briefs.append({"id": f"cb-stock-dex-daily-{latest['day']}", "kind": "daily", "text": build_daily(latest)})
    state = load_json(STATE_PATH)
    sent = {x for x in state.get("sentIds", []) if isinstance(x, str)}
    if args.history_scan:
        n = 0
        for day in reversed(ordered[-HISTORY_DAYS:]):
            if day == ordered[-1] or n >= HISTORY_LIMIT:
                continue
            view = view_of(days, ordered, day)
            if not isinstance(view["dod"], float) or abs(view["dod"]) < MAJOR_PCT:
                continue
            msg_id = f"cb-stock-dex-threshold-{view['day']}"
            if msg_id in sent and not args.force:
                continue
            briefs.append({"id": msg_id, "kind": "threshold", "text": build_threshold(view, historical=True)})
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
            {"sentIds": sent_ids[-200:], "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
