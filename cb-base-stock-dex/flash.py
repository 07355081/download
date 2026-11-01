# -*- coding: utf-8 -*-
"""Coinbase Base 股票代币 DEX 成交 → Lark。

- 日常：最近一个已抓到的 UTC 日【实时日常报告】
- 阈值：|较前一日| ≥ 40% → 【历史阈值警报】+【特殊警报】
- --history-scan：回扫近 40 天里过线且未发过的，标明历史回扫
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
DATA = HERE / "output" / "json" / "daily.json"
STATE = HERE / "output" / "cb_stock_dex_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/cb-base-stock-dex"
MAJOR_PCT = 40.0
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

def load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def merge_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        env.update(load_env_file(path))
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


def fmt_wan(n: float) -> str:
    if abs(n) >= 1e8:
        return f"{n / 1e8:.2f} 亿美元"
    return f"{n / 1e4:.0f} 万美元"


def fmt_pct(n: float) -> str:
    return f"{'+' if n > 0 else ''}{n:.1f}%"


def view_of(rows: list[dict[str, Any]], index: int) -> dict[str, Any]:
    row = rows[index]
    prev = rows[index - 1] if index else None
    total = float(row.get("total") or 0)
    dod = None
    if prev and float(prev.get("total") or 0) > 0:
        dod = (total / float(prev["total"]) - 1) * 100
    by_dex = row.get("by_dex") if isinstance(row.get("by_dex"), dict) else {}
    aero = float(by_dex.get("Aerodrome") or 0)
    uni4 = float(by_dex.get("Uniswap v4") or 0)
    share = aero / total * 100 if total else 0
    top = row.get("top") if isinstance(row.get("top"), list) else []
    return {
        "day": str(row.get("day")),
        "total": total,
        "prev": None if prev is None else str(prev.get("day")),
        "dod": dod,
        "aero": aero,
        "uni4": uni4,
        "share": share,
        "top": top[:3],
    }


def top_text(view: dict[str, Any]) -> str:
    bits = []
    for item in view["top"]:
        if not isinstance(item, dict):
            continue
        bits.append(f"{item.get('symbol')} {fmt_wan(float(item.get('usd') or 0))}")
    return "、".join(bits)


def build_daily(view: dict[str, Any]) -> str:
    dod = view["dod"]
    dod_s = f"，较前一日（{view['prev']}）{fmt_pct(float(dod))}" if isinstance(dod, float) else ""
    title = f"Coinbase 股票代币 Base DEX 近 24 小时成交 {fmt_wan(view['total'])}"
    if isinstance(dod, float):
        title += f"，环比 {fmt_pct(dod)}"
    body = (
        f"据吴说数据中心统计，截至 {view['day']}（UTC）这次抓取，"
        f"Coinbase 在 Base 上发行的股票代币，各 DEX 池近 24 小时成交合计 {fmt_wan(view['total'])}{dod_s}。"
        f"其中 Aerodrome {fmt_wan(view['aero'])}（占 {view['share']:.1f}%），Uniswap v4 {fmt_wan(view['uni4'])}。"
    )
    names = top_text(view)
    if names:
        body += f"成交靠前的是 {names}。"
    body += "口径是 GeckoTerminal 池子 24 小时成交按池子去重，不是 Token Terminal 的 30 天逐笔合计。"
    return f"{title}\n{body}\n{PAGE}"


def build_threshold(view: dict[str, Any], *, historical: bool) -> str:
    dod = float(view["dod"])
    body = (
        f"【特殊】Coinbase 股票代币 Base 成交异动：{view['day']} 环比 {fmt_pct(dod)}\n"
        f"吴说获悉，据吴说数据中心统计，{view['day']}（UTC）Coinbase Base 股票代币 DEX 近 24 小时成交 "
        f"{fmt_wan(view['total'])}，较前一日（{view['prev']}）{fmt_pct(dod)}，绝对值达到 {MAJOR_PCT:g}%。"
        f"Aerodrome 占 {view['share']:.1f}%。本条不是每日例行稿。"
    )
    if historical:
        found = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
        body += f"本条为历史回扫发现（日期 {view['day']}，发现时间 {found} UTC+8），请勿当成刚抓到的一天。"
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
    doc = load_json(DATA)
    rows = [row for row in doc.get("days") or [] if isinstance(row, dict) and row.get("day") and float(row.get("total") or 0) > 0]
    rows.sort(key=lambda row: str(row.get("day")))
    if not rows:
        print("[ERROR] 没有成交数据")
        return 1
    latest = view_of(rows, len(rows) - 1)
    print(f"[cb-stock-dex] latest={latest['day']} total={latest['total']:.0f} aero={latest['share']:.1f}% dod={latest['dod']}", flush=True)
    briefs: list[dict[str, Any]] = []
    if isinstance(latest["dod"], float) and abs(latest["dod"]) >= MAJOR_PCT:
        briefs.append({"id": f"cb-stock-dex-threshold-{latest['day']}", "kind": "threshold", "text": build_threshold(latest, historical=False)})
    if not args.no_daily:
        briefs.append({"id": f"cb-stock-dex-daily-{latest['day']}", "kind": "daily", "text": build_daily(latest)})
    state = load_json(STATE)
    sent = {item for item in state.get("sentIds", []) if isinstance(item, str)}
    if args.history_scan:
        n = 0
        for index in range(len(rows) - 2, -1, -1):
            if n >= HISTORY_LIMIT:
                break
            view = view_of(rows, index)
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
    env = merge_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK")
        return 2
    sent_ids = [item for item in state.get("sentIds", []) if isinstance(item, str)]
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
        save_json(STATE, {"sentIds": sent_ids[-200:], "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
