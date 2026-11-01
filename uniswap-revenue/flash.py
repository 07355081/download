# -*- coding: utf-8 -*-
"""Uniswap 协议收入按链（RH 占比）→ Lark。

口径：DefiLlama Uniswap dailyRevenue 按链汇总，不是 Token Terminal 原表。

- 日常：每个刚闭合自然月【实时日常报告】（总收入 + RH 金额/占比 + 是否最大来源）
- 阈值：【历史阈值警报】——RH 成 Top1、占比≥阈值、RH 收入环比≥阈值
- --history-scan：回扫未推阈值条，抬头标明历史回扫

用法：
  python flash.py
  python flash.py --push
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
    HERE / "output" / "json" / "uni_revenue_by_chain.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "uni_revenue_by_chain.json",
    Path("/root/dashboard/public/json/uni_revenue_by_chain.json"),
]
STATE_PATH = HERE / "output" / "uni_revenue_lark.json"
LOG_PATH = HERE / "flash.log"

ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

TZ_CN = timezone(timedelta(hours=8))
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/robinhood-chain"
FOCUS = "Robinhood Chain"

DEFAULT_SHARE_PCT = 50.0
DEFAULT_MOM_PCT = 100.0
MIN_RH_USD_FOR_ALERT = 1_000_000.0
HISTORY_SCAN_MONTHS = 12
DEFAULT_HISTORY_LIMIT = 4


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
    for key in (
        "LARK_ALERT_WEBHOOK",
        "LARK_ALERT_SECRET",
        "UNI_REV_SHARE_PCT",
        "UNI_REV_MOM_PCT",
    ):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def env_float(env: dict[str, str], key: str, default: float) -> float:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def resolve_json_path(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 uni_revenue_by_chain.json，请先跑 download.py")


def load_months(path: Path) -> list[dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    months = doc.get("months") if isinstance(doc, dict) else None
    if not isinstance(months, list) or not months:
        raise RuntimeError(f"{path} 无 months")
    out: list[dict[str, Any]] = []
    for row in months:
        if not isinstance(row, dict):
            continue
        month = str(row.get("month") or "")[:7]
        if len(month) != 7:
            continue
        out.append(
            {
                "month": month,
                "revenue_usd": float(row.get("revenue_usd") or 0),
                "rh_revenue_usd": float(row.get("rh_revenue_usd") or 0),
                "rh_share_pct": float(row.get("rh_share_pct") or 0),
                "top1_chain": str(row.get("top1_chain") or ""),
                "top1_revenue_usd": float(row.get("top1_revenue_usd") or 0),
                "rh_is_top1": bool(row.get("rh_is_top1")),
                "complete": bool(row.get("complete")),
                "days": int(row.get("days") or 0),
            }
        )
    out.sort(key=lambda r: r["month"])
    return out


def complete_months(months: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [m for m in months if m["complete"]]


def newest_complete(months: list[dict[str, Any]]) -> dict[str, Any] | None:
    done = complete_months(months)
    return done[-1] if done else None


def prior_month(months: list[dict[str, Any]], month: str) -> dict[str, Any] | None:
    done = [m for m in complete_months(months) if m["month"] < month]
    return done[-1] if done else None


def fmt_yi(usd: float) -> str:
    return f"{usd / 1e8:.2f} 亿美金" if usd >= 1e8 else f"{usd / 1e4:.0f} 万美元"


def fmt_pct(pct: float | None) -> str:
    if pct is None:
        return "—"
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def mom_pct(curr: float, prev: float) -> float | None:
    if prev <= 0:
        return None
    return (curr / prev - 1.0) * 100.0


def evaluate_threshold(
    months: list[dict[str, Any]],
    month: str,
    *,
    share_line: float,
    mom_line: float,
) -> tuple[bool, list[str]]:
    done = complete_months(months)
    curr = next((m for m in done if m["month"] == month), None)
    if not curr:
        return False, []
    reasons: list[str] = []
    prev = prior_month(months, month)

    if curr["rh_is_top1"] and curr["rh_revenue_usd"] >= MIN_RH_USD_FOR_ALERT:
        earlier_top = any(
            m["rh_is_top1"] and m["rh_revenue_usd"] >= MIN_RH_USD_FOR_ALERT
            for m in done
            if m["month"] < month
        )
        if not earlier_top:
            reasons.append(
                f"{FOCUS} 首次成为 Uniswap 协议收入最大来源（约 {fmt_yi(curr['rh_revenue_usd'])}，"
                f"占 {curr['rh_share_pct']:.1f}%）"
            )
        elif prev and not prev["rh_is_top1"]:
            reasons.append(
                f"{FOCUS} 重新成为最大收入来源（约 {fmt_yi(curr['rh_revenue_usd'])}，"
                f"占 {curr['rh_share_pct']:.1f}%）"
            )

    if curr["rh_share_pct"] >= share_line and curr["rh_revenue_usd"] >= MIN_RH_USD_FOR_ALERT:
        earlier_cross = any(
            m["rh_share_pct"] >= share_line and m["rh_revenue_usd"] >= MIN_RH_USD_FOR_ALERT
            for m in done
            if m["month"] < month
        )
        if not earlier_cross:
            reasons.append(
                f"{FOCUS} 收入占比首次达到或超过 {share_line:g}%（当月 {curr['rh_share_pct']:.1f}%）"
            )

    if (
        prev
        and curr["rh_revenue_usd"] >= MIN_RH_USD_FOR_ALERT
        and prev["rh_revenue_usd"] >= MIN_RH_USD_FOR_ALERT
    ):
        pct = mom_pct(curr["rh_revenue_usd"], prev["rh_revenue_usd"])
        if pct is not None and pct >= mom_line:
            reasons.append(
                f"{FOCUS} 协议收入相对 {prev['month']} 环比 {fmt_pct(pct)}（阈值 {mom_line:g}%）"
            )

    return bool(reasons), reasons


def build_monthly_daily_text(months: list[dict[str, Any]], month: str) -> str:
    curr = next(m for m in months if m["month"] == month)
    prev = prior_month(months, month)
    y, mo = int(month[:4]), int(month[5:7])
    top_bit = (
        f"已是当月最大收入来源（超过 {curr['top1_chain'] if not curr['rh_is_top1'] else '其余各链'}）。"
        if curr["rh_is_top1"]
        else f"当月最大来源为 {curr['top1_chain']}（约 {fmt_yi(curr['top1_revenue_usd'])}）。"
    )
    if curr["rh_is_top1"]:
        top_bit = f"已成为当月最大收入来源（占比 {curr['rh_share_pct']:.1f}%）。"
    mom_bit = ""
    if prev:
        mom = mom_pct(curr["rh_revenue_usd"], prev["rh_revenue_usd"])
        mom_bit = f"相对 {prev['month']} 月，{FOCUS} 协议收入环比 {fmt_pct(mom)}。"
    title = (
        f"Uniswap {y} 年 {mo} 月协议收入约 {fmt_yi(curr['revenue_usd'])}，"
        f"{FOCUS} 约占 {curr['rh_share_pct']:.0f}%"
    )
    body = (
        f"据吴说数据中心统计（DefiLlama Uniswap 协议收入口径），{y} 年 {mo} 月 "
        f"Uniswap 协议收入合计约 {fmt_yi(curr['revenue_usd'])}，其中部署在 {FOCUS} 的收入约 "
        f"{fmt_yi(curr['rh_revenue_usd'])}，约占 {curr['rh_share_pct']:.1f}%。"
        f"{top_bit}{mom_bit}"
        f"本条为刚闭合自然月例行摘要；与 Token Terminal「收入」同量级但口径可能不完全一致。"
        f"\n{DASHBOARD_URL}"
    )
    return f"{title}\n{body}"


def build_threshold_text(
    months: list[dict[str, Any]],
    month: str,
    reasons: list[str],
    *,
    historical: bool,
) -> str:
    curr = next(m for m in months if m["month"] == month)
    y, mo = int(month[:4]), int(month[5:7])
    title = f"Uniswap 协议收入异动：{y} 年 {mo} 月 {FOCUS} 触发阈值"
    body = (
        f"吴说获悉，DefiLlama 统计的 Uniswap 协议收入在 {y} 年 {mo} 月出现超阈值变化。"
        f"触发原因：{'；'.join(reasons)}。"
        f"当月协议总收入约 {fmt_yi(curr['revenue_usd'])}，{FOCUS} 约 {fmt_yi(curr['rh_revenue_usd'])}"
        f"（占 {curr['rh_share_pct']:.1f}%）。"
        f"本条为阈值异动提醒，不是例行日报；口径为 Uniswap 协议收入按链，非 Token Terminal 原表，亦非链 gas。"
    )
    if historical:
        found = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
        body += f"本条为历史回扫发现（数据月 {month}，发现时间 {found} UTC+8），请勿当成今日刚发生。"
    body += f"\n{DASHBOARD_URL}"
    return f"{title}\n{body}"


def with_mark(mark: str, text: str) -> str:
    return f"{mark}\n{text}"


def gen_lark_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark_text(webhook: str, secret: str | None, text: str) -> None:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = gen_lark_sign(secret, ts)
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
        return {"sentIds": [], "updatedAt": ""}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": [], "updatedAt": ""}
    if not isinstance(doc, dict):
        return {"sentIds": [], "updatedAt": ""}
    ids = doc.get("sentIds")
    return {
        "sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else [],
        "updatedAt": str(doc.get("updatedAt") or ""),
    }


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def append_log(stamp: str, msg_id: str, ok: bool, text: str, extra: str = "") -> None:
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} id={msg_id} ok={ok} {extra}\n{text}\n\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--history-scan", action="store_true")
    parser.add_argument("--history-limit", type=int, default=DEFAULT_HISTORY_LIMIT)
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    if getattr(args, "history_scan", False):
        args.history_scan = False

    env = merge_env()
    share_line = env_float(env, "UNI_REV_SHARE_PCT", DEFAULT_SHARE_PCT)
    mom_line = env_float(env, "UNI_REV_MOM_PCT", DEFAULT_MOM_PCT)

    try:
        json_path = resolve_json_path(args.json or None)
        months = load_months(json_path)
    except (OSError, RuntimeError, FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    newest = newest_complete(months)
    print(
        f"[uni-rev] json={json_path} months={len(months)} "
        f"newest_complete={newest['month'] if newest else None} "
        f"share_line={share_line:g}% mom_line={mom_line:g}%"
    )

    briefs: list[dict[str, Any]] = []
    if newest:
        month = newest["month"]
        hit, reasons = evaluate_threshold(
            months, month, share_line=share_line, mom_line=mom_line
        )
        if hit:
            briefs.append(
                {
                    "id": f"uni-rev-threshold-{month}",
                    "kind": "threshold",
                    "historical": False,
                    "text": build_threshold_text(months, month, reasons, historical=False),
                }
            )
        briefs.append(
            {
                "id": f"uni-rev-monthly-{month}",
                "kind": "daily",
                "historical": False,
                "text": build_monthly_daily_text(months, month),
            }
        )

    state = load_state()
    sent_set = set(state.get("sentIds") or [])

    if args.history_scan:
        done = complete_months(months)
        scan = done[-HISTORY_SCAN_MONTHS:]
        hist_n = 0
        limit = max(0, int(args.history_limit))
        for item in reversed(scan):
            month = item["month"]
            if newest and month == newest["month"]:
                continue
            if hist_n >= limit:
                break
            msg_id = f"uni-rev-threshold-{month}"
            if msg_id in sent_set and not args.force:
                continue
            ok, why = evaluate_threshold(
                months, month, share_line=share_line, mom_line=mom_line
            )
            if not ok:
                continue
            briefs.append(
                {
                    "id": msg_id,
                    "kind": "threshold",
                    "historical": True,
                    "text": build_threshold_text(months, month, why, historical=True),
                }
            )
            hist_n += 1

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for brief in briefs:
        if brief["id"] in seen:
            continue
        seen.add(brief["id"])
        unique.append(brief)
    unique.sort(key=lambda b: (0 if b["kind"] == "threshold" else 1, b["id"]))

    for brief in unique:
        mark = LARK_MARK_THRESHOLD if brief["kind"] == "threshold" else LARK_MARK_DAILY
        text = with_mark(mark, brief["text"])
        already = brief["id"] in state["sentIds"]
        print("---")
        print(
            f"id={brief['id']} kind={brief['kind']} historical={brief['historical']} already={already}"
        )
        print(text)

    if not args.push:
        print("\n[dry-run] 未推送")
        return 0

    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2

    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    sent_any = False
    for brief in unique:
        if brief["id"] in state["sentIds"] and not args.force:
            print(f"skip {brief['id']}")
            continue
        mark = LARK_MARK_THRESHOLD if brief["kind"] == "threshold" else LARK_MARK_DAILY
        text = with_mark(mark, brief["text"])
        try:
            send_lark_text(webhook, secret, text)
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            print(f"[ERROR] Lark 失败 {brief['id']}: {exc}", file=sys.stderr)
            append_log(stamp, brief["id"], False, text, extra=f"err={exc}")
            return 1
        print(f"  → Lark OK {brief['id']}")
        if brief["id"] not in state["sentIds"]:
            state["sentIds"].append(brief["id"])
        sent_any = True
        append_log(stamp, brief["id"], True, text)
        time.sleep(0.4)

    if sent_any:
        state["sentIds"] = state["sentIds"][-120:]
        save_state(state)
        print(f"[uni-rev] state → {STATE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
