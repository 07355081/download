# -*- coding: utf-8 -*-
"""协议 YTD 收入榜（DefiLlama 近似）→ Lark。

- 日常：【实时日常报告】当年初至昨日 Top10
- 阈值：【历史阈值警报】榜首易主，或前三名单变化
- 特殊：【特殊警报】有项目新进前五
- --history-scan：回扫未推送阈值/特殊

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
    HERE / "output" / "json" / "protocol_revenue_ytd.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "protocol-revenue-ytd" / "protocol_revenue_ytd.json",
    Path("/root/dashboard/public/json/protocol-revenue-ytd/protocol_revenue_ytd.json"),
]
STATE_PATH = HERE / "output" / "protocol_revenue_ytd_lark.json"
LOG_PATH = HERE / "protocol_revenue_ytd_flash.log"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

TZ_CN = timezone(timedelta(hours=8))
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
LARK_MARK_SPECIAL = "【特殊警报】"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/lark-feeds/protocol-revenue-ytd"
HISTORY_SCAN_DAYS = 60
DEFAULT_HISTORY_LIMIT = 3


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
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def resolve_json_path(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 protocol_revenue_ytd.json，请先跑 download.py")


def load_doc(path: Path) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("ranking"), list):
        raise RuntimeError("invalid json")
    return doc


def fmt_usd(n: float) -> str:
    a = abs(n)
    if a >= 1e8:
        return f"{n / 1e8:.2f} 亿美元"
    if a >= 1e7:
        return f"{n / 1e8:.2f} 亿美元"
    if a >= 1e4:
        return f"{n / 1e4:.0f} 万美元"
    return f"{n:,.0f} 美元"


def cn_day(day: str) -> str:
    y, m, d = day.split("-")
    return f"{int(y)} 年 {int(m)} 月 {int(d)} 日"


def name_map(doc: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in doc.get("protocols") or []:
        if isinstance(p, dict) and p.get("slug"):
            out[str(p["slug"])] = str(p.get("name_zh") or p.get("name") or p["slug"])
    for r in doc.get("ranking") or []:
        if isinstance(r, dict) and r.get("slug"):
            out[str(r["slug"])] = str(r.get("name_zh") or r.get("name") or r["slug"])
    return out


def hist_by_day(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in doc.get("history") or []:
        if isinstance(row, dict) and row.get("time"):
            out[str(row["time"])[:10]] = row
    return out


def prev_hist(hmap: dict[str, dict[str, Any]], day: str) -> dict[str, Any] | None:
    days = sorted(d for d in hmap if d < day)
    return hmap[days[-1]] if days else None


def build_daily_text(doc: dict[str, Any]) -> str:
    start = str(doc.get("window_start") or "")
    end = str(doc.get("window_end") or "")
    ranking = [r for r in (doc.get("ranking") or []) if isinstance(r, dict)]
    top10 = ranking[:10]
    parts = []
    for r in top10:
        parts.append(
            f"{r.get('name_zh') or r.get('name')} {fmt_usd(float(r.get('revenue') or 0))}"
        )
    body = (
        f"据吴说数据中心（DefiLlama 协议收入口径）统计，"
        f"{cn_day(start)}至{cn_day(end)}，固定观察名单链上项目收入前十依次为："
        f"{'、'.join(parts)}。"
        "该排名不含 Tether、Circle 和 Grayscale。"
        "口径为 DefiLlama dailyRevenue 窗口加总，近似公开收入榜，不是 CoinGecko API；"
        "Axiom 等交易终端可能与外电偏差较大。"
        f"\n{DASHBOARD_URL}"
    )
    return body


def evaluate_threshold(doc: dict[str, Any], day: str) -> tuple[bool, str]:
    hmap = hist_by_day(doc)
    cur = hmap.get(day)
    prev = prev_hist(hmap, day)
    if not cur or not prev:
        return False, "无对照日"
    reasons: list[str] = []
    labels = name_map(doc)
    if cur.get("top1") and cur.get("top1") != prev.get("top1"):
        reasons.append(
            f"榜首由 {labels.get(str(prev.get('top1')), prev.get('top1'))} "
            f"换为 {labels.get(str(cur.get('top1')), cur.get('top1'))}"
        )
    prev3 = tuple(prev.get("top3") or [])
    cur3 = tuple(cur.get("top3") or [])
    if prev3 and cur3 and prev3 != cur3 and not reasons:
        reasons.append(f"前三名单变化：{'/'.join(labels.get(s, s) for s in cur3)}")
    elif prev3 and cur3 and prev3 != cur3:
        reasons.append("前三名单同时变化")
    return bool(reasons), "；".join(reasons) if reasons else "未过线"


def evaluate_special(doc: dict[str, Any], day: str) -> tuple[bool, str, str]:
    hmap = hist_by_day(doc)
    cur = hmap.get(day)
    prev = prev_hist(hmap, day)
    if not cur or not prev:
        return False, "", ""
    prev5 = set(prev.get("top5") or [])
    cur5 = list(cur.get("top5") or [])
    newcomers = [s for s in cur5 if s not in prev5]
    if not newcomers:
        return False, "", ""
    labels = name_map(doc)
    names = "、".join(labels.get(s, s) for s in newcomers)
    return True, f"新进前五：{names}", f"top5-{day}-{'-'.join(newcomers)}"


def with_mark(mark: str, text: str) -> str:
    if text.startswith(mark):
        return text
    return f"{mark}\n{text}"


def gen_sign(secret: str, timestamp: int) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark_text(webhook: str, secret: str | None, text: str) -> dict[str, Any]:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = int(time.time())
        payload["timestamp"] = str(ts)
        payload["sign"] = gen_sign(secret, ts)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": []}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": []}
    ids = doc.get("sentIds") if isinstance(doc, dict) else None
    return {"sentIds": [str(x) for x in ids] if isinstance(ids, list) else []}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def append_log(stamp: str, msg_id: str, ok: bool, text: str, extra: str = "") -> None:
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} id={msg_id} ok={ok} {extra}\n{text}\n\n")


def mark_for(kind: str) -> str:
    if kind == "special":
        return LARK_MARK_SPECIAL
    if kind == "threshold":
        return LARK_MARK_THRESHOLD
    return LARK_MARK_DAILY


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--history-scan", action="store_true")
    parser.add_argument("--history-limit", type=int, default=DEFAULT_HISTORY_LIMIT)
    parser.add_argument("--json", default="")
    parser.add_argument("--no-daily", action="store_true")
    args = parser.parse_args()
    if getattr(args, "history_scan", False):
        args.history_scan = False

    env = merge_env()
    try:
        json_path = resolve_json_path(args.json or None)
        doc = load_doc(json_path)
    except (OSError, RuntimeError, FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    end = str(doc.get("window_end") or "")
    print(f"[protocol-revenue-ytd] json={json_path} window_end={end}")

    briefs: list[dict[str, Any]] = []
    hit_s, why_s, suf = evaluate_special(doc, end)
    if hit_s and suf:
        briefs.append(
            {
                "id": f"protocol-revenue-ytd-special-{suf}",
                "kind": "special",
                "text": f"观察名单 YTD 收入榜出现特殊变化：{why_s}。" + build_daily_text(doc),
            }
        )

    hit_t, why_t = evaluate_threshold(doc, end)
    if hit_t:
        briefs.append(
            {
                "id": f"protocol-revenue-ytd-threshold-{end}",
                "kind": "threshold",
                "text": f"观察名单 YTD 收入榜超阈值变动：{why_t}。" + build_daily_text(doc),
            }
        )

    if not args.no_daily:
        briefs.append(
            {
                "id": f"protocol-revenue-ytd-daily-{end}",
                "kind": "daily",
                "text": build_daily_text(doc),
            }
        )

    state = load_state()
    sent_set = set(state.get("sentIds") or [])
    hmap = hist_by_day(doc)

    if args.history_scan:
        days = sorted(hmap)
        hist_n = 0
        limit = max(0, int(args.history_limit))
        for day in reversed(days):
            if day == end or hist_n >= limit:
                continue
            ok_s, why, suf = evaluate_special(doc, day)
            if ok_s and suf:
                msg_id = f"protocol-revenue-ytd-special-{suf}"
                if msg_id not in sent_set or args.force:
                    # rebuild text for that day would need ranking_at; use why + note
                    briefs.append(
                        {
                            "id": msg_id,
                            "kind": "special",
                            "text": (
                                f"（历史回扫，截至 {cn_day(day)}，不是当日例行连推）"
                                f"观察名单 YTD 收入榜出现特殊变化：{why}。"
                                "详见当时快照，不复述今日榜单。"
                                f"\n{DASHBOARD_URL}"
                            ),
                        }
                    )
                    hist_n += 1
                    if hist_n >= limit:
                        continue
            ok_t, why_t = evaluate_threshold(doc, day)
            if not ok_t:
                continue
            msg_id = f"protocol-revenue-ytd-threshold-{day}"
            if msg_id in sent_set and not args.force:
                continue
            briefs.append(
                {
                    "id": msg_id,
                    "kind": "threshold",
                    "text": (
                        f"（历史回扫，截至 {cn_day(day)}，不是当日例行连推）"
                        f"观察名单 YTD 收入榜超阈值变动：{why_t}。"
                        f"\n{DASHBOARD_URL}"
                    ),
                }
            )
            hist_n += 1

    order = {"special": 0, "threshold": 1, "daily": 2}
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for brief in sorted(briefs, key=lambda b: (order.get(b["kind"], 9), b["id"])):
        if brief["id"] in seen:
            continue
        seen.add(brief["id"])
        unique.append(brief)

    for brief in unique:
        text = with_mark(mark_for(brief["kind"]), brief["text"])
        print("---")
        print(f"id={brief['id']} kind={brief['kind']} already={brief['id'] in sent_set}")
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
        text = with_mark(mark_for(brief["kind"]), brief["text"])
        try:
            send_lark_text(webhook, secret, text)
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
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
        state["sentIds"] = state["sentIds"][-160:]
        save_state(state)
        print(f"[protocol-revenue-ytd] state → {STATE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
