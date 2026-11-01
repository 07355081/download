# -*- coding: utf-8 -*-
"""比特币单周涨跌 → Lark。

- 日常：最近一个已结束周一条【实时日常报告】（同一周只发一次）
- 阈值：创已存序列单周美元新高，或 2023-03 以来最大单周百分比
  → 【历史阈值警报】并加【特殊警报】
- --history-scan：回扫近若干周里过线、尚未推送的周，标明历史回扫

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
    HERE / "output" / "json" / "btc_weekly_move.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "btc-weekly-move" / "btc_weekly_move.json",
    Path("/root/dashboard/public/json/btc-weekly-move/btc_weekly_move.json"),
]
STATE_PATH = HERE / "output" / "btc_weekly_move_lark.json"
LOG_PATH = HERE / "btc_weekly_move_flash.log"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
LARK_MARK_SPECIAL = "【特殊警报】"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/btc-indicators"
JSON_URL = "https://data.wublock123.com/dashboard/btc-indicators"
HISTORY_WEEKS = 16


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


def resolve_json(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 btc_weekly_move.json")


def fmt_px(n: float) -> str:
    return f"{n:,.0f}"


def fmt_usd_delta(n: float) -> str:
    sign = "上涨" if n >= 0 else "下跌"
    return f"{sign} {abs(n):,.0f} 美元"


def fmt_pct(n: float) -> str:
    sign = "+" if n > 0 else ""
    return f"{sign}{n:.1f}%"


def cn_span(row: dict[str, Any]) -> str:
    a = str(row["week_start"])
    b = str(row["week_end"])
    y1, m1, d1 = a.split("-")
    y2, m2, d2 = b.split("-")
    if y1 == y2:
        return f"{int(y1)} 年 {int(m1)} 月 {int(d1)} 日至 {int(m2)} 月 {int(d2)} 日"
    return f"{int(y1)} 年 {int(m1)} 月 {int(d1)} 日至 {int(y2)} 年 {int(m2)} 月 {int(d2)} 日"


def still_special(row: dict[str, Any], weeks: list[dict[str, Any]]) -> bool:
    """现在仍是美元新高，或仍是 2023-03 以来百分比最大的那一周。"""
    if int(row.get("usd_rank") or 0) == 1 and float(row.get("usd") or 0) > 0:
        return True
    if not row.get("best_pct_since_2023_03"):
        return False
    pct = float(row["pct"])
    for other in weeks:
        if str(other["week_end"]) > str(row["week_end"]) and float(other["pct"]) > pct:
            return False
    return True


def reasons(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if row.get("new_usd_high"):
        out.append(f"单周美元变动 {fmt_usd_delta(float(row['usd']))}，创已存序列新高")
    if row.get("best_pct_since_2023_03"):
        out.append(f"单周涨跌 {fmt_pct(float(row['pct']))}，为 2023 年 3 月以来已存序列最大")
    return out


def build_daily(row: dict[str, Any], n_weeks: int) -> str:
    title = f"比特币单周涨跌：{fmt_usd_delta(float(row['usd']))}"
    body = (
        f"据吴说数据中心统计（Yahoo BTC-USD 日线，上周日收盘至本周日收盘），"
        f"{cn_span(row)}比特币从约 {fmt_px(float(row['start_px']))} 美元"
        f"至约 {fmt_px(float(row['end_px']))} 美元，单周{fmt_usd_delta(float(row['usd']))}，"
        f"涨跌约 {fmt_pct(float(row['pct']))}。"
        f"按美元变动在已存 {n_weeks} 个完整周中排第 {int(row['usd_rank'])}，"
        f"按百分比排第 {int(row['pct_rank'])}。"
        f"口径为 Yahoo 日收盘，与 Galaxy 等第三方小时点可能不一致。"
        f"\n{DASHBOARD_URL}"
    )
    return f"{title}\n{body}"


def build_threshold(row: dict[str, Any], *, historical: bool) -> str:
    title = f"【特殊】比特币单周美元涨幅异动：{cn_span(row)}"
    body = (
        f"吴说获悉，据吴说数据中心（Yahoo BTC-USD 日线）统计，{cn_span(row)}"
        f"比特币单周出现需单独提示的变化。"
        f"触发原因：{'；'.join(reasons(row))}。"
        f"当周从约 {fmt_px(float(row['start_px']))} 美元至约 {fmt_px(float(row['end_px']))} 美元，"
        f"百分比约 {fmt_pct(float(row['pct']))}，按百分比在已存序列中排第 {int(row['pct_rank'])}。"
        f"本条不是每周例行简报；不宜与 Galaxy 原文数字直接对表。"
    )
    if historical:
        found = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
        body += f"本条为历史回扫发现（周结束日 {row['week_end']}，发现时间 {found} UTC+8），请勿当成刚发生的一周。"
    body += f"\n{DASHBOARD_URL}"
    return f"{title}\n{body}"


def with_mark(mark: str, text: str) -> str:
    return f"{mark}\n{text}"


def mark_for(kind: str) -> str:
    if kind == "threshold":
        return f"{LARK_MARK_THRESHOLD}\n{LARK_MARK_SPECIAL}"
    return LARK_MARK_DAILY


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
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} id={msg_id} ok={ok}\n{text}\n\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--history-scan", action="store_true")
    parser.add_argument("--json", default="")
    parser.add_argument("--no-daily", action="store_true")
    args = parser.parse_args()
    if getattr(args, "history_scan", False):
        args.history_scan = False

    try:
        path = resolve_json(args.json or None)
        doc = json.loads(path.read_text(encoding="utf-8"))
        weeks = [w for w in doc.get("weeks") or [] if isinstance(w, dict) and w.get("week_end")]
        weeks.sort(key=lambda w: str(w["week_end"]))
        if not weeks:
            raise RuntimeError("weeks 为空")
    except (OSError, json.JSONDecodeError, RuntimeError, FileNotFoundError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    latest = weeks[-1]
    n = int(doc.get("week_count") or len(weeks))
    print(
        f"[btc-weekly-move] json={path} latest={latest['week_end']} "
        f"usd={float(latest['usd']):.0f} rank={latest.get('usd_rank')} special={still_special(latest, weeks)}"
    )

    briefs: list[dict[str, Any]] = []
    if still_special(latest, weeks):
        briefs.append(
            {
                "id": f"btc-weekly-threshold-{latest['week_end']}",
                "kind": "threshold",
                "historical": False,
                "text": build_threshold(latest, historical=False),
            }
        )
    if not args.no_daily:
        briefs.append(
            {
                "id": f"btc-weekly-daily-{latest['week_end']}",
                "kind": "daily",
                "historical": False,
                "text": build_daily(latest, n),
            }
        )

    state = load_state()
    sent = set(state["sentIds"])
    if args.history_scan:
        for row in weeks[-HISTORY_WEEKS:]:
            if row["week_end"] == latest["week_end"] or not still_special(row, weeks):
                continue
            msg_id = f"btc-weekly-threshold-{row['week_end']}"
            if msg_id in sent and not args.force:
                continue
            briefs.append(
                {
                    "id": msg_id,
                    "kind": "threshold",
                    "historical": True,
                    "text": build_threshold(row, historical=True),
                }
            )

    order = {"threshold": 0, "daily": 1}
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for brief in sorted(briefs, key=lambda b: (order.get(b["kind"], 9), str(b["id"]))):
        if brief["id"] in seen:
            continue
        seen.add(brief["id"])
        unique.append(brief)

    for brief in unique:
        print("---")
        print(f"id={brief['id']} kind={brief['kind']} historical={brief['historical']} already={brief['id'] in sent}")
        print(with_mark(mark_for(brief["kind"]), brief["text"]))

    if not args.push:
        print("\n[dry-run] 未推送")
        return 0

    env = merge_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2

    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    sent_any = False
    for brief in unique:
        if brief["id"] in state["sentIds"] and not args.force:
            print(f"[skip] already {brief['id']}")
            continue
        text = with_mark(mark_for(brief["kind"]), brief["text"])
        try:
            send_lark(webhook, secret, text)
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] Lark 失败 {brief['id']}: {exc}", file=sys.stderr)
            append_log(stamp, brief["id"], False, text)
            return 1
        print(f"[pushed] {brief['id']}")
        if brief["id"] not in state["sentIds"]:
            state["sentIds"].append(brief["id"])
        sent_any = True
        append_log(stamp, brief["id"], True, text)
        time.sleep(0.4)
    if sent_any:
        state["sentIds"] = state["sentIds"][-200:]
        save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
