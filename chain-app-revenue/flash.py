# -*- coding: utf-8 -*-
"""各链 24h 应用收入 → Lark。

口径：DefiLlama dailyRevenue 按链加总，不是公链 gas。

- 日常：每个上海自然日一条【实时日常报告】
- 阈值：第一名换链，或前五里某链日环比绝对值 ≥ 25%
- 特殊：第一名收入创已存快照新高时，阈值条再加【特殊警报】
- --history-scan：回扫未推阈值，标明历史回扫

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
    HERE / "output" / "json" / "chain_app_revenue_latest.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "chain_app_revenue_latest.json",
    Path("/root/dashboard/public/json/chain_app_revenue_latest.json"),
]
HISTORY_DIRS = [
    HERE / "output" / "json" / "chain_app_revenue_history",
    ROOT.parent / "data-dashboard" / "public" / "json" / "chain_app_revenue_history",
    Path("/root/dashboard/public/json/chain_app_revenue_history"),
]
STATE_PATH = HERE / "output" / "chain_app_revenue_lark.json"
LOG_PATH = HERE / "flash.log"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
LARK_MARK_SPECIAL = "【特殊警报】"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/lark-feeds/chain-app-revenue"
DEFAULT_DOD_PCT = 25.0
HISTORY_SCAN_DAYS = 30
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
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET", "CHAIN_APP_REVENUE_DOD_PCT"):
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


def resolve_latest(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 chain_app_revenue_latest.json")


def resolve_history_dir() -> Path | None:
    for path in HISTORY_DIRS:
        if path.is_dir():
            return path
    return None


def load_snap(path: Path) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    day = str(doc.get("day") or "")[:10]
    top = doc.get("top") if isinstance(doc.get("top"), list) else []
    chains = {str(row.get("id")): row for row in top if isinstance(row, dict) and row.get("id")}
    leader = top[0] if top else {}
    return {
        "day": day,
        "top": top,
        "by_id": chains,
        "leader_id": str(leader.get("id") or ""),
        "leader_name": str(leader.get("name") or ""),
        "leader_usd": float(leader.get("revenue_usd") or 0),
    }


def load_history(limit: int = HISTORY_SCAN_DAYS) -> list[dict[str, Any]]:
    hist_dir = resolve_history_dir()
    if not hist_dir:
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(hist_dir.glob("????-??-??.json"))[-limit:]:
        try:
            out.append(load_snap(path))
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            continue
    return out


def fmt_wan(usd: float) -> str:
    return f"{usd / 1e4:.1f} 万美元"


def top_phrase(top: list[Any], limit: int = 5) -> str:
    bits = []
    for row in top[:limit]:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        try:
            usd = float(row.get("revenue_usd") or 0)
        except (TypeError, ValueError):
            continue
        if name:
            bits.append(f"{name} {fmt_wan(usd)}")
    return "，".join(bits) if bits else "无"


def evaluate_threshold(
    curr: dict[str, Any],
    history: list[dict[str, Any]],
    *,
    dod_line: float,
) -> tuple[bool, list[str], bool]:
    earlier = [row for row in history if row["day"] < curr["day"] and row["leader_usd"] > 0]
    if not earlier:
        return False, [], False
    prev = earlier[-1]
    reasons: list[str] = []
    special = False
    if curr["leader_id"] and curr["leader_id"] != prev["leader_id"]:
        reasons.append(
            f"应用收入第一名由 {prev['leader_name']} 换成 {curr['leader_name']}"
            f"（{fmt_wan(curr['leader_usd'])}）"
        )
    for row in (curr.get("top") or [])[:5]:
        if not isinstance(row, dict):
            continue
        chain_id = str(row.get("id") or "")
        prev_row = prev["by_id"].get(chain_id)
        if not prev_row:
            continue
        try:
            now_usd = float(row.get("revenue_usd") or 0)
            prev_usd = float(prev_row.get("revenue_usd") or 0)
        except (TypeError, ValueError):
            continue
        if prev_usd <= 0 or now_usd < 200_000:
            continue
        dod = (now_usd / prev_usd - 1.0) * 100.0
        if abs(dod) >= dod_line:
            reasons.append(
                f"{row.get('name')} 日环比 {dod:+.1f}%（{fmt_wan(prev_usd)} → {fmt_wan(now_usd)}，线 ±{dod_line:g}%）"
            )
    if reasons:
        max_leader = max(row["leader_usd"] for row in earlier)
        if curr["leader_usd"] > max_leader * 1.001:
            special = True
            reasons.append(
                f"第一名 {curr['leader_name']} 的 24 小时应用收入 {fmt_wan(curr['leader_usd'])}"
                f" 创已存快照新高（此前最高 {fmt_wan(max_leader)}）"
            )
    return bool(reasons), reasons, special


def build_daily_text(snap: dict[str, Any]) -> str:
    day = snap["day"]
    y, m, d = day.split("-")
    title = f"{int(m)} 月 {int(d)} 日各链应用收入：{snap['leader_name']} 约 {fmt_wan(snap['leader_usd'])} 居前"
    body = (
        f"据吴说数据中心统计（DefiLlama 协议收入按链加总，滚动 24 小时，不含公链 gas），"
        f"截至 {y}-{m}-{d}，应用收入靠前的是 {top_phrase(snap['top'])}。"
        f"本条为当日例行摘要；24 小时窗口每天都在变，与某一篇旧新闻里的金额不必相同。"
        f"\n{DASHBOARD_URL}"
    )
    return f"{title}\n{body}"


def build_threshold_text(snap: dict[str, Any], reasons: list[str], *, historical: bool) -> str:
    day = snap["day"]
    y, m, d = int(day[:4]), int(day[5:7]), int(day[8:10])
    title = f"各链应用收入异动：{y} 年 {m} 月 {d} 日触发阈值"
    body = (
        f"吴说获悉，DefiLlama 统计的各链 24 小时应用收入在 {y}-{m:02d}-{d:02d} 出现超阈值变化。"
        f"触发原因：{'；'.join(reasons)}。"
        f"当日靠前：{top_phrase(snap['top'])}。"
        f"本条不是例行日报，也不是公链 gas。口径为协议收入按链加总。"
    )
    if historical:
        found = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
        body += f"本条为历史回扫发现（数据日 {day}，发现时间 {found} UTC+8），请勿当成今日刚发生。"
    body += f"\n{DASHBOARD_URL}"
    return f"{title}\n{body}"


def mark_for(brief: dict[str, Any]) -> str:
    if brief["kind"] != "threshold":
        return LARK_MARK_DAILY
    if brief.get("special"):
        return f"{LARK_MARK_THRESHOLD}\n{LARK_MARK_SPECIAL}"
    return LARK_MARK_THRESHOLD


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
        return {"sentIds": []}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": []}
    ids = doc.get("sentIds") if isinstance(doc, dict) else []
    return {"sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else []}


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
    parser = argparse.ArgumentParser()
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--history-scan", action="store_true")
    parser.add_argument("--history-limit", type=int, default=DEFAULT_HISTORY_LIMIT)
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    if getattr(args, "history_scan", False):
        args.history_scan = False
    env = merge_env()
    dod_line = env_float(env, "CHAIN_APP_REVENUE_DOD_PCT", DEFAULT_DOD_PCT)
    try:
        latest_path = resolve_latest(args.json or None)
        curr = load_snap(latest_path)
        history = load_history()
    except (OSError, RuntimeError, FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1
    print(
        f"[chain-app-revenue] day={curr['day']} leader={curr['leader_name']} "
        f"{curr['leader_usd']/1e4:.1f}万 hist={len(history)}"
    )
    briefs: list[dict[str, Any]] = []
    earlier = [row for row in history if row["day"] < curr["day"]]
    hit, reasons, special = evaluate_threshold(curr, earlier, dod_line=dod_line)
    if hit:
        briefs.append(
            {
                "id": f"chain-app-revenue-threshold-{curr['day']}",
                "kind": "threshold",
                "historical": False,
                "special": special,
                "text": build_threshold_text(curr, reasons, historical=False),
            }
        )
    briefs.append(
        {
            "id": f"chain-app-revenue-daily-{curr['day']}",
            "kind": "daily",
            "historical": False,
            "special": False,
            "text": build_daily_text(curr),
        }
    )
    state = load_state()
    sent = set(state["sentIds"])
    if args.history_scan:
        hist_n = 0
        for snap in reversed(history):
            if snap["day"] == curr["day"] or hist_n >= args.history_limit:
                continue
            msg_id = f"chain-app-revenue-threshold-{snap['day']}"
            if msg_id in sent and not args.force:
                continue
            prior = [row for row in history if row["day"] < snap["day"]]
            ok, why, sp = evaluate_threshold(snap, prior, dod_line=dod_line)
            if not ok:
                continue
            briefs.append(
                {
                    "id": msg_id,
                    "kind": "threshold",
                    "historical": True,
                    "special": sp,
                    "text": build_threshold_text(snap, why, historical=True),
                }
            )
            hist_n += 1
    seen: set[str] = set()
    unique = []
    for brief in sorted(briefs, key=lambda item: (0 if item["kind"] == "threshold" else 1, item["id"])):
        if brief["id"] in seen:
            continue
        seen.add(brief["id"])
        unique.append(brief)
    for brief in unique:
        print("---")
        print(f"id={brief['id']} kind={brief['kind']} special={brief['special']} already={brief['id'] in sent}")
        print(f"{mark_for(brief)}\n{brief['text']}")
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
        if brief["id"] in sent and not args.force:
            print(f"skip {brief['id']}")
            continue
        text = f"{mark_for(brief)}\n{brief['text']}"
        try:
            send_lark_text(webhook, secret, text)
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            append_log(stamp, brief["id"], False, text, extra=str(exc))
            print(f"[ERROR] {brief['id']}: {exc}", file=sys.stderr)
            return 1
        print(f"  → Lark OK {brief['id']}")
        sent.add(brief["id"])
        sent_any = True
        append_log(stamp, brief["id"], True, text)
        time.sleep(0.4)
    if sent_any:
        state["sentIds"] = sorted(sent)[-180:]
        save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
