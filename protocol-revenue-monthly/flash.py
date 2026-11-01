# -*- coding: utf-8 -*-
"""协议月收入榜 → Lark。

- 日常：最近一个已闭合 UTC 月一条【实时日常报告】
- 阈值：前 15 中任一协议环比增收 ≥ 1000 万美元，或前 15 里环比增长家数 ≥ 8
  →【历史阈值警报】+【特殊警报】
- --history-scan：近若干闭合月里过线且未发过的，标明历史回扫

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
    HERE / "output" / "json" / "protocol_revenue_monthly.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "protocol-revenue-monthly" / "protocol_revenue_monthly.json",
    Path("/root/dashboard/public/json/protocol-revenue-monthly/protocol_revenue_monthly.json"),
]
STATE_PATH = HERE / "output" / "protocol_revenue_monthly_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/protocol-revenue-monthly"
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
LARK_MARK_SPECIAL = "【特殊警报】"
HISTORY_MONTHS = 6
MOM_USD = 10_000_000.0
GAINERS = 8


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
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def merge_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        env.update(load_env_file(path))
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def env_float(env: dict[str, str], key: str, default: float) -> float:
    raw = (env.get(key) or "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def load_doc() -> dict[str, Any]:
    for path in JSON_CANDIDATES:
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                return doc
    raise FileNotFoundError("找不到 protocol_revenue_monthly.json")


def fmt_usd(n: float) -> str:
    a = abs(float(n))
    if a >= 1e8:
        return f"{a / 1e8:.3f} 亿美元"
    return f"{a / 1e4:.0f} 万美元"


def month_label(ym: str) -> str:
    y, m = ym.split("-")
    return f"{int(y)} 年 {int(m)} 月"


def reasons(month: dict[str, Any], mom_line: float, gainer_line: float) -> list[str]:
    out: list[str] = []
    gainers = int(month.get("top15_gainers") or 0)
    if gainers >= gainer_line:
        out.append(f"前 15 中有 {gainers} 家环比增长（线 ≥{gainer_line:g}）")
    for row in month.get("top") or []:
        if not isinstance(row, dict):
            continue
        delta = row.get("mom_usd")
        if isinstance(delta, (int, float)) and float(delta) >= mom_line:
            out.append(f"{row.get('name')} 环比增收 {fmt_usd(float(delta))}（线 ≥{fmt_usd(mom_line)}）")
    return out


def build_daily(month: dict[str, Any]) -> str:
    label = month_label(str(month["month"]))
    top = [r for r in (month.get("top") or []) if isinstance(r, dict)]
    gainers = int(month.get("top15_gainers") or 0)
    head = []
    for row in top[:2]:
        head.append(f"{row.get('name')} {fmt_usd(float(row.get('revenue_usd') or 0))}")
    jumps = sorted(
        (
            r
            for r in top
            if isinstance(r.get("mom_usd"), (int, float)) and float(r["mom_usd"]) > 0
        ),
        key=lambda r: float(r["mom_usd"]),
        reverse=True,
    )[:2]
    jump_txt = ""
    if len(jumps) >= 2:
        a, b = jumps[0], jumps[1]
        both = float(a["mom_usd"]) + float(b["mom_usd"])
        jump_txt = (
            f"{a.get('name')} 收入 {fmt_usd(float(a.get('revenue_usd') or 0))}，"
            f"环比增加 {fmt_usd(float(a['mom_usd']))}；"
            f"{b.get('name')} 收入 {fmt_usd(float(b.get('revenue_usd') or 0))}，"
            f"环比增加 {fmt_usd(float(b['mom_usd']))}，"
            f"两者增收合计 {fmt_usd(both)}。"
        )
    elif len(jumps) == 1:
        a = jumps[0]
        jump_txt = (
            f"{a.get('name')} 收入 {fmt_usd(float(a.get('revenue_usd') or 0))}，"
            f"环比增加 {fmt_usd(float(a['mom_usd']))}。"
        )
    body = (
        f"据吴说数据中心统计，{label}收入排名前 15 的协议中，"
        f"有 {gainers} 家实现环比增长。"
        + jump_txt
        + (f"{'、'.join(head)}位居前两位。" if head else "")
        + "口径为 UTC 自然月收入，不是滚动 30 日；与 DeFiLlama 社交稿的切月和合并方式可能仍有差异。"
    )
    return f"协议月收入：{label}\n{body}\n{PAGE}"


def build_threshold(month: dict[str, Any], why: list[str], *, historical: bool) -> str:
    label = month_label(str(month["month"]))
    body = (
        "不是日常新闻，是历史阈值警报。\n"
        f"据吴说数据中心统计，{label}协议收入榜出现超阈值变化。"
        f"触发原因：{'；'.join(why)}。"
        "本条为阈值异动提醒，不是每月例行简报。"
    )
    if historical:
        found = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
        body += f"本条为历史回扫发现（月份 {month['month']}，发现时间 {found} UTC+8），请勿当成刚闭合的月份。"
    return f"协议月收入异动：{label}\n{body}\n{PAGE}"


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
        code = result.get("code", result.get("StatusCode", 0))
        if code not in (0, "0", None):
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--history-scan", action="store_true")
    args = parser.parse_args()
    if getattr(args, "history_scan", False):
        args.history_scan = False
    env = merge_env()
    mom_line = env_float(env, "PROTOCOL_REVENUE_MONTHLY_MOM_USD", MOM_USD)
    gainer_line = env_float(env, "PROTOCOL_REVENUE_MONTHLY_GAINERS", GAINERS)
    doc = load_doc()
    months = [m for m in (doc.get("months") or []) if isinstance(m, dict) and m.get("month")]
    if not months:
        print("[ERROR] 没有闭合月", file=sys.stderr)
        return 1
    latest = months[-1]
    briefs: list[dict[str, Any]] = []

    def add(month: dict[str, Any], *, historical: bool) -> None:
        ym = str(month["month"])
        why = reasons(month, mom_line, gainer_line)
        if why:
            briefs.append(
                {
                    "id": f"protocol-revenue-monthly-threshold-{ym}",
                    "kind": "threshold",
                    "text": build_threshold(month, why, historical=historical),
                }
            )
        if not historical:
            briefs.append(
                {
                    "id": f"protocol-revenue-monthly-daily-{ym}",
                    "kind": "daily",
                    "text": build_daily(month),
                }
            )

    add(latest, historical=False)
    if args.history_scan:
        for month in months[-HISTORY_MONTHS:-1]:
            add(month, historical=True)

    state = load_state()
    print(f"[protocol-revenue-monthly] latest={latest['month']} briefs={len(briefs)}")
    for brief in briefs:
        mark = (
            f"{LARK_MARK_THRESHOLD}\n{LARK_MARK_SPECIAL}"
            if brief["kind"] == "threshold"
            else LARK_MARK_DAILY
        )
        print("---")
        print(brief["id"], "already", brief["id"] in state["sentIds"])
        print(f"{mark}\n{brief['text']}")

    if not args.push:
        print("\n[dry-run] 未推送")
        return 0
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    sent = False
    for brief in briefs:
        if brief["id"] in state["sentIds"] and not args.force:
            print(f"skip {brief['id']}")
            continue
        mark = (
            f"{LARK_MARK_THRESHOLD}\n{LARK_MARK_SPECIAL}"
            if brief["kind"] == "threshold"
            else LARK_MARK_DAILY
        )
        try:
            send_lark(webhook, secret, f"{mark}\n{brief['text']}")
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] Lark 失败 {brief['id']}: {exc}", file=sys.stderr)
            return 1
        print(f"  → Lark OK {brief['id']}")
        if brief["id"] not in state["sentIds"]:
            state["sentIds"].append(brief["id"])
        sent = True
        time.sleep(0.4)
    if sent:
        state["sentIds"] = state["sentIds"][-80:]
        save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
