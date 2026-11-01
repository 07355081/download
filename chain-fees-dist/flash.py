# -*- coding: utf-8 -*-
"""公链 24h 手续费长尾分档 → Lark。

口径：DefiLlama overview/fees 中 protocolType=chain 的 total24h（滚动 24h）。

- 日常：每个上海自然日一条【实时日常报告】（分档 + ≥10 万美元名单）
- 阈值：【历史阈值警报】——0 手续费链数创新高、≥10 万链数变化、lt_1k 占比创新高
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
    HERE / "output" / "json" / "chain_fees_dist_latest.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "chain_fees_dist_latest.json",
    Path("/root/dashboard/public/json/chain_fees_dist_latest.json"),
]
HISTORY_DIRS = [
    HERE / "output" / "json" / "chain_fees_dist_history",
    ROOT.parent / "data-dashboard" / "public" / "json" / "chain_fees_dist_history",
    Path("/root/dashboard/public/json/chain_fees_dist_history"),
]
STATE_PATH = HERE / "output" / "chain_fees_dist_lark.json"
LOG_PATH = HERE / "flash.log"

ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

TZ_CN = timezone(timedelta(hours=8))
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/lark-feeds/chain-fees-dist"
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
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def resolve_latest(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 chain_fees_dist_latest.json，请先跑 download.py")


def resolve_history_dir() -> Path | None:
    for path in HISTORY_DIRS:
        if path.is_dir():
            return path
    return None


def load_snap(path: Path) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise RuntimeError(f"{path} 不是对象")
    day = str(doc.get("day") or "")[:10]
    if len(day) != 10:
        raise RuntimeError(f"{path} 缺 day")
    return {
        "day": day,
        "universe": int(doc.get("universe") or 0),
        "lt_1k": int(doc.get("lt_1k") or 0),
        "lt_10": int(doc.get("lt_10") or 0),
        "eq_0": int(doc.get("eq_0") or 0),
        "ge_100k": int(doc.get("ge_100k") or 0),
        "top": doc.get("top") if isinstance(doc.get("top"), list) else [],
        "updated_at": str(doc.get("updated_at") or ""),
    }


def load_history(limit: int = HISTORY_SCAN_DAYS) -> list[dict[str, Any]]:
    hist_dir = resolve_history_dir()
    if not hist_dir:
        return []
    files = sorted(hist_dir.glob("????-??-??.json"))[-limit:]
    out: list[dict[str, Any]] = []
    for path in files:
        try:
            out.append(load_snap(path))
        except (OSError, RuntimeError, json.JSONDecodeError, ValueError):
            continue
    return out


def fmt_yi(usd: float) -> str:
    if usd >= 1e8:
        return f"{usd / 1e8:.2f} 亿美金"
    if usd >= 1e4:
        return f"{usd / 1e4:.0f} 万美元"
    return f"{usd:.0f} 美元"


def top_phrase(top: list[Any], *, limit: int = 7) -> str:
    bits: list[str] = []
    for row in top[:limit]:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        try:
            fees = float(row.get("fees_usd") or 0)
        except (TypeError, ValueError):
            continue
        if not name:
            continue
        bits.append(f"{name} {fmt_yi(fees)}")
    return "，".join(bits) if bits else "无"


def share_pct(part: int, whole: int) -> float:
    return (part / whole * 100.0) if whole > 0 else 0.0


def evaluate_threshold(
    curr: dict[str, Any], history: list[dict[str, Any]]
) -> tuple[bool, list[str]]:
    """history 含更早日期（不含 curr.day）。"""
    reasons: list[str] = []
    earlier = [h for h in history if h["day"] < curr["day"] and h["universe"] > 0]
    if not earlier:
        return False, []

    max_zero = max(h["eq_0"] for h in earlier)
    if curr["eq_0"] > max_zero and curr["eq_0"] >= 50:
        reasons.append(
            f"手续费为 0 的公链数 {curr['eq_0']} 条创近 {len(earlier)} 日新高（此前最高 {max_zero}）"
        )

    max_lt1k_share = max(share_pct(h["lt_1k"], h["universe"]) for h in earlier)
    curr_lt1k_share = share_pct(curr["lt_1k"], curr["universe"])
    if curr_lt1k_share > max_lt1k_share + 0.5 and curr_lt1k_share >= 80:
        reasons.append(
            f"手续费 <1k 美元的公链占比 {curr_lt1k_share:.1f}% 创近 {len(earlier)} 日新高"
        )

    prev = earlier[-1]
    if abs(curr["ge_100k"] - prev["ge_100k"]) >= 2:
        direction = "增至" if curr["ge_100k"] > prev["ge_100k"] else "降至"
        reasons.append(
            f"手续费 ≥10 万美元的公链数相对 {prev['day']} {direction} {curr['ge_100k']} 条"
            f"（前值 {prev['ge_100k']}）"
        )

    return bool(reasons), reasons


def build_daily_text(snap: dict[str, Any]) -> str:
    day = snap["day"]
    y, m, d = day.split("-")
    title = (
        f"{int(m)} 月 {int(d)} 日公链手续费长尾：{snap['universe']} 条中 "
        f"{snap['lt_1k']} 条 <1k 美元"
    )
    top = top_phrase(snap["top"])
    body = (
        f"据吴说数据中心统计（DefiLlama 公链手续费口径，滚动 24 小时），截至 {y}-{m}-{d}，"
        f"纳入统计的公链共 {snap['universe']} 条：其中 {snap['lt_1k']} 条手续费收入小于 1k 美元，"
        f"{snap['lt_10']} 条小于 10 美元，{snap['eq_0']} 条为 0；"
        f"手续费收入大于或等于 10 万美元的公链共 {snap['ge_100k']} 条"
        + (f"，分别是 {top}。" if snap["ge_100k"] else "。")
        + "本条为当日例行分档摘要；公链宇宙随 DefiLlama 适配器变化，与个别新闻稿条数可能不完全一致。"
        f"\n{DASHBOARD_URL}"
    )
    return f"{title}\n{body}"


def build_threshold_text(
    snap: dict[str, Any], reasons: list[str], *, historical: bool
) -> str:
    day = snap["day"]
    y, m, d = int(day[:4]), int(day[5:7]), int(day[8:10])
    title = f"公链手续费长尾异动：{y} 年 {m} 月 {d} 日触发阈值"
    body = (
        f"吴说获悉，DefiLlama 统计的公链 24 小时手续费分布在 {y}-{m:02d}-{d:02d} 出现超阈值变化。"
        f"触发原因：{'；'.join(reasons)}。"
        f"当日宇宙 {snap['universe']} 条：<1k 美元 {snap['lt_1k']} 条、<10 美元 {snap['lt_10']} 条、"
        f"为 0 共 {snap['eq_0']} 条；≥10 万美元 {snap['ge_100k']} 条"
        + (f"（{top_phrase(snap['top'])}）" if snap["ge_100k"] else "")
        + "。本条为阈值异动提醒，不是例行日报；口径为公链 gas/手续费，非协议收入榜。"
    )
    if historical:
        found = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
        body += f"本条为历史回扫发现（数据日 {day}，发现时间 {found} UTC+8），请勿当成今日刚发生。"
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
    try:
        latest_path = resolve_latest(args.json or None)
        curr = load_snap(latest_path)
        history = load_history()
    except (OSError, RuntimeError, FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    print(
        f"[chain-fees-dist] json={latest_path} day={curr['day']} "
        f"universe={curr['universe']} hist={len(history)}"
    )

    briefs: list[dict[str, Any]] = []
    earlier = [h for h in history if h["day"] < curr["day"]]
    hit, reasons = evaluate_threshold(curr, earlier)
    if hit:
        briefs.append(
            {
                "id": f"chain-fees-dist-threshold-{curr['day']}",
                "kind": "threshold",
                "historical": False,
                "text": build_threshold_text(curr, reasons, historical=False),
            }
        )
    briefs.append(
        {
            "id": f"chain-fees-dist-daily-{curr['day']}",
            "kind": "daily",
            "historical": False,
            "text": build_daily_text(curr),
        }
    )

    state = load_state()
    sent_set = set(state.get("sentIds") or [])

    if args.history_scan:
        hist_n = 0
        limit = max(0, int(args.history_limit))
        # older first for scan from recent
        for snap in reversed(history):
            day = snap["day"]
            if day == curr["day"]:
                continue
            if hist_n >= limit:
                break
            msg_id = f"chain-fees-dist-threshold-{day}"
            if msg_id in sent_set and not args.force:
                continue
            prior = [h for h in history if h["day"] < day]
            ok, why = evaluate_threshold(snap, prior)
            if not ok:
                continue
            briefs.append(
                {
                    "id": msg_id,
                    "kind": "threshold",
                    "historical": True,
                    "text": build_threshold_text(snap, why, historical=True),
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
        state["sentIds"] = state["sentIds"][-180:]
        save_state(state)
        print(f"[chain-fees-dist] state → {STATE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
