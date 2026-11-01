# -*- coding: utf-8 -*-
"""Scroll DeFi 锁仓与链收入 → Lark。

- 日常：【实时日常报告】最新已闭合日的锁仓、距峰值跌幅、近 30 天链收入
- 阈值：【历史阈值警报】锁仓或近 30 天链收入创出已存新低，且低于此前低点至少 5%
- 创低点时同一条再加【特殊警报】
- 不回扫旧日期，10 月 3 日那篇不补推

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
    HERE / "output" / "json" / "scroll_l2.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "scroll-l2" / "scroll_l2.json",
    Path("/root/dashboard/public/json/scroll-l2/scroll_l2.json"),
]
STATE_PATH = HERE / "output" / "scroll_l2_lark.json"
LOG_PATH = HERE / "scroll_l2_flash.log"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

TZ_CN = timezone(timedelta(hours=8))
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
LARK_MARK_SPECIAL = "【特殊警报】"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/lark-feeds/scroll-l2"
PEAK_LABEL = "2024 年 10 月 17 日"
MIN_PRIOR = 30
BREAK_PCT = 5.0  # 新低须比此前低点再低这么多，避免每天微跌都报


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
    raise FileNotFoundError("找不到 scroll_l2.json，请先跑 download.py")


def load_doc(path: Path) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("history"), list):
        raise RuntimeError("invalid json")
    return doc


def hist_rows(doc: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [r for r in doc.get("history") or [] if isinstance(r, dict) and r.get("time")]
    rows.sort(key=lambda r: str(r["time"]))
    return rows


def closed_row(rows: list[dict[str, Any]], today: str) -> dict[str, Any] | None:
    closed = [r for r in rows if str(r["time"]) < today and r.get("tvl") is not None]
    return closed[-1] if closed else None


def fmt_usd(n: float) -> str:
    sign = "-" if n < 0 else ""
    a = abs(n)
    if a >= 1e8:
        return f"{sign}{a / 1e8:.2f} 亿美元"
    if a >= 1e4:
        return f"{sign}{a / 1e4:.0f} 万美元"
    if abs(a - round(a)) < 0.05:
        return f"{sign}{a:.0f} 美元"
    return f"{sign}{a:.1f} 美元"


def cn_day(day: str) -> str:
    y, m, d = day.split("-")
    return f"{y} 年 {int(m)} 月 {int(d)} 日"


def broke_low(current: float, prior: list[float]) -> bool:
    if len(prior) < MIN_PRIOR:
        return False
    floor = min(prior)
    if floor == 0:
        return current < 0
    return current < floor and (floor - current) / abs(floor) * 100.0 >= BREAK_PCT


def evaluate(doc: dict[str, Any], row: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[bool, str, bool]:
    day = str(row["time"])
    earlier = [r for r in rows if str(r["time"]) < day]
    reasons: list[str] = []
    tvl = float(row.get("tvl") or 0)
    prior_tvl = [float(r["tvl"]) for r in earlier if isinstance(r.get("tvl"), (int, float))]
    if broke_low(tvl, prior_tvl):
        reasons.append(
            f"锁仓 {fmt_usd(tvl)} 低于此前低点 {fmt_usd(min(prior_tvl))} 至少 {BREAK_PCT:g}%"
        )
    rev = row.get("revenue_30d")
    prior_rev = [
        float(r["revenue_30d"])
        for r in earlier
        if isinstance(r.get("revenue_30d"), (int, float))
    ]
    if isinstance(rev, (int, float)) and broke_low(float(rev), prior_rev):
        reasons.append(
            f"近 30 天链收入 {fmt_usd(float(rev))} 低于此前低点 {fmt_usd(min(prior_rev))} 至少 {BREAK_PCT:g}%"
        )
    return bool(reasons), "；".join(reasons), bool(reasons)


def build_core(doc: dict[str, Any], row: dict[str, Any]) -> str:
    tvl = float(row.get("tvl") or 0)
    peak = float(doc.get("peak_tvl") or 0)
    drop = (peak - tvl) / peak * 100.0 if peak else 0.0
    rev30 = row.get("revenue_30d")
    avg = row.get("revenue_30d_avg")
    rev_txt = "近 30 天链收入暂缺"
    if isinstance(rev30, (int, float)) and isinstance(avg, (int, float)):
        rev_txt = f"近 30 天链收入合计约 {fmt_usd(float(rev30))}，日均约 {fmt_usd(float(avg))}"
    return (
        f"据吴说数据中心统计，截至 {cn_day(str(row['time']))}，"
        f"Scroll 的 DeFi 锁仓约 {fmt_usd(tvl)}，"
        f"较 {PEAK_LABEL}峰值约 {fmt_usd(peak)}，下降约 {drop:.1f}%。"
        f"{rev_txt}。"
        "链收入是这条链自己的手续费减去提交以太坊的成本，可以为负。"
        f"\n{DASHBOARD_URL}"
    )


def with_mark(mark: str, text: str) -> str:
    return text if text.startswith(mark) else f"{mark}\n{text}"


def gen_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode("utf-8"), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark_text(webhook: str, secret: str | None, text: str) -> None:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = gen_sign(secret, ts)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if isinstance(body, dict) and body.get("code") not in (0, None):
        raise RuntimeError(body)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": []}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": []}
    ids = doc.get("sentIds") if isinstance(doc, dict) else []
    if not isinstance(ids, list):
        ids = []
    return {"sentIds": [str(x) for x in ids]}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def append_log(stamp: str, msg_id: str, ok: bool, text: str, extra: str = "") -> None:
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} id={msg_id} ok={ok} {extra}\n{text}\n\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", default="")
    parser.add_argument("--no-daily", action="store_true")
    args = parser.parse_args()

    try:
        path = resolve_json_path(args.json or None)
        doc = load_doc(path)
        rows = hist_rows(doc)
    except (OSError, RuntimeError, FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    today = datetime.now(timezone.utc).date().isoformat()
    row = closed_row(rows, today)
    if row is None:
        print("[ERROR] 没有已闭合的锁仓日", file=sys.stderr)
        return 1
    day = str(row["time"])
    age = (datetime.now(timezone.utc).date() - datetime.strptime(day, "%Y-%m-%d").date()).days
    print(f"[scroll-l2] json={path} closed={day} age={age}d")
    if age > 3:
        print("[ERROR] 数据超过 3 天，不发", file=sys.stderr)
        return 1

    briefs: list[dict[str, Any]] = []
    hit, why, record = evaluate(doc, row, rows)
    if hit:
        head = "这是历史低点，不是日常新闻。" if record else ""
        briefs.append(
            {
                "id": f"scroll-l2-threshold-{day}",
                "kind": "threshold",
                "record": record,
                "text": f"{head}Scroll 锁仓或链收入创出新低：{why}。" + build_core(doc, row),
            }
        )
    if not args.no_daily:
        briefs.append(
            {
                "id": f"scroll-l2-daily-{day}",
                "kind": "daily",
                "record": False,
                "text": build_core(doc, row),
            }
        )

    state = load_state()
    sent = set(state["sentIds"])
    for brief in briefs:
        mark = LARK_MARK_DAILY
        if brief["kind"] == "threshold":
            mark = f"{LARK_MARK_THRESHOLD}\n{LARK_MARK_SPECIAL}" if brief["record"] else LARK_MARK_THRESHOLD
        text = with_mark(mark, brief["text"])
        print("---")
        print(f"id={brief['id']} already={brief['id'] in sent}")
        print(text)
        brief["text"] = text

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
    for brief in briefs:
        if brief["id"] in state["sentIds"] and not args.force:
            print(f"skip {brief['id']}")
            continue
        try:
            send_lark_text(webhook, secret, brief["text"])
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] Lark 失败 {brief['id']}: {exc}", file=sys.stderr)
            append_log(stamp, brief["id"], False, brief["text"], extra=f"err={exc}")
            return 1
        print(f"  → Lark OK {brief['id']}")
        if brief["id"] not in state["sentIds"]:
            state["sentIds"].append(brief["id"])
        sent_any = True
        append_log(stamp, brief["id"], True, brief["text"])
        time.sleep(0.4)
    if sent_any:
        state["sentIds"] = state["sentIds"][-160:]
        save_state(state)
        print(f"[scroll-l2] state → {STATE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
