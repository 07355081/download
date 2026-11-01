# -*- coding: utf-8 -*-
"""Layer 2 代币总市值 → Lark。

口径：CoinGecko categories id=layer-2，不是 CryptoRank。

- 日常：【实时日常报告】最新快照总市值 + Top 币 + 日环比
- 阈值：【历史阈值警报】|日环比|≥线，或创已存序列新高/新低
- 特殊：创已存新高，或从已存低点反弹越过阈值时加【特殊警报】
- --history-scan：回扫未推送的阈值条，抬头标明历史回扫

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
    HERE / "output" / "json" / "l2_token_mcap.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "l2-token-mcap" / "l2_token_mcap.json",
    Path("/root/dashboard/public/json/l2-token-mcap/l2_token_mcap.json"),
]
STATE_PATH = HERE / "output" / "l2_token_mcap_lark.json"
LOG_PATH = HERE / "l2_token_mcap_flash.log"

ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

TZ_CN = timezone(timedelta(hours=8))
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
LARK_MARK_SPECIAL = "【特殊警报】"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/lark-feeds/l2-token-mcap"

DEFAULT_DOD_PCT = 5.0
MIN_HISTORY_FOR_EXTREME = 14
HISTORY_SCAN_DAYS = 90
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
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET", "L2_TOKEN_MCAP_DOD_PCT"):
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
    raise FileNotFoundError("找不到 l2_token_mcap.json，请先跑 download.py")


def load_doc(path: Path) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("data"), list):
        raise RuntimeError("invalid json")
    return doc


def rows_of(doc: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        r
        for r in doc.get("data") or []
        if isinstance(r, dict)
        and r.get("time")
        and isinstance(r.get("market_cap"), (int, float))
    ]
    rows.sort(key=lambda r: str(r["time"]))
    return rows


def row_by_day(rows: list[dict[str, Any]], day: str) -> dict[str, Any] | None:
    for row in rows:
        if str(row["time"]) == day:
            return row
    return None


def pct_change(curr: float, prev: float) -> float | None:
    if prev == 0:
        return None
    return (curr - prev) / abs(prev) * 100.0


def fmt_yi(n: float) -> str:
    """亿美元。"""
    sign = "-" if n < 0 else ""
    a = abs(n) / 1e8
    if a >= 100:
        return f"{sign}{a:.0f} 亿美元"
    if a >= 10:
        return f"{sign}{a:.1f} 亿美元"
    return f"{sign}{a:.2f} 亿美元"


def fmt_pct(n: float) -> str:
    sign = "+" if n > 0 else ""
    return f"{sign}{n:.1f}%"


def cn_day(day: str) -> str:
    y, m, d = day.split("-")
    return f"{int(y)} 年 {int(m)} 月 {int(d)} 日"


def evaluate_threshold(
    rows: list[dict[str, Any]], day: str, *, dod_line: float
) -> tuple[bool, list[str], bool]:
    curr = row_by_day(rows, day)
    if not curr:
        return False, [], False
    idx = next(i for i, r in enumerate(rows) if str(r["time"]) == day)
    mcap = float(curr["market_cap"])
    reasons: list[str] = []
    special = False

    if idx > 0:
        prev = float(rows[idx - 1]["market_cap"])
        dod = pct_change(mcap, prev)
        if dod is not None and abs(dod) >= dod_line:
            # 连续同向超阈值只报首日：前一日已超同向线则跳过
            skip = False
            if idx >= 2:
                prev2 = float(rows[idx - 2]["market_cap"])
                prev_dod = pct_change(prev, prev2)
                if (
                    prev_dod is not None
                    and abs(prev_dod) >= dod_line
                    and (prev_dod > 0) == (dod > 0)
                ):
                    skip = True
            if not skip:
                reasons.append(
                    f"总市值日环比 {fmt_pct(dod)}（现约 {fmt_yi(mcap)}，线 ±{dod_line:g}%）"
                )

    earlier = [float(r["market_cap"]) for r in rows[:idx]]
    if len(earlier) >= MIN_HISTORY_FOR_EXTREME:
        hi = max(earlier)
        lo = min(earlier)
        if mcap > hi:
            reasons.append(f"总市值 {fmt_yi(mcap)} 创已存序列新高（此前高点 {fmt_yi(hi)}）")
            special = True
        elif mcap < lo:
            reasons.append(f"总市值 {fmt_yi(mcap)} 创已存序列新低（此前低点 {fmt_yi(lo)}）")
            special = True
        elif lo > 0 and (mcap - lo) / lo * 100.0 >= max(dod_line * 2, 15.0) and idx > 0:
            # 从已存低点反弹足够大，且当日仍在涨
            prev = float(rows[idx - 1]["market_cap"])
            if mcap > prev:
                rebound = (mcap - lo) / lo * 100.0
                reasons.append(
                    f"总市值自已存低点 {fmt_yi(lo)} 反弹约 {fmt_pct(rebound)} 至 {fmt_yi(mcap)}"
                )
                special = True

    # de-dupe
    seen: set[str] = set()
    uniq: list[str] = []
    for r in reasons:
        if r in seen:
            continue
        seen.add(r)
        uniq.append(r)
    return bool(uniq), uniq, special


def build_daily_text(doc: dict[str, Any], rows: list[dict[str, Any]], day: str) -> str:
    curr = row_by_day(rows, day)
    assert curr is not None
    mcap = float(curr["market_cap"])
    title = f"Layer 2 代币总市值：约 {fmt_yi(mcap)}"
    dod_s = ""
    idx = next(i for i, r in enumerate(rows) if str(r["time"]) == day)
    if idx > 0:
        prev = float(rows[idx - 1]["market_cap"])
        dod = pct_change(mcap, prev)
        if dod is not None:
            dod_s = f"，较前一快照日 {fmt_pct(dod)}"
    elif isinstance(curr.get("market_cap_change_24h"), (int, float)):
        dod_s = f"，CoinGecko 分类 24h 变动约 {fmt_pct(float(curr['market_cap_change_24h']))}"

    top = doc.get("latest_top") if isinstance(doc.get("latest_top"), list) else []
    top_bits: list[str] = []
    for item in top[:5]:
        if not isinstance(item, dict):
            continue
        sym = str(item.get("symbol") or "")
        val = item.get("market_cap")
        if sym and isinstance(val, (int, float)):
            top_bits.append(f"{sym} 约 {fmt_yi(float(val))}")
    top_s = "；".join(top_bits) if top_bits else "—"

    body = (
        f"据吴说数据中心统计（CoinGecko Layer 2 (L2) 分类），截至 {cn_day(day)} UTC 快照，"
        f"Layer 2 代币总市值约 {fmt_yi(mcap)}{dod_s}。"
        f"市值靠前约：{top_s}。"
        f"口径为 CoinGecko，与 CryptoRank 等外电板块数字可能不一致；"
        f"历史序列自接入日起日更累加。"
        f"\n{DASHBOARD_URL}"
    )
    return f"{title}\n{body}"


def build_threshold_text(
    rows: list[dict[str, Any]],
    day: str,
    reasons: list[str],
    *,
    historical: bool,
    special: bool,
) -> str:
    curr = row_by_day(rows, day) or {}
    mcap = float(curr.get("market_cap") or 0)
    y, m, d = day.split("-")
    title = f"Layer 2 代币市值异动：{int(m)} 月 {int(d)} 日触发阈值"
    if special:
        title = f"【特殊】{title}"
    body = (
        f"吴说获悉，据吴说数据中心（CoinGecko Layer 2 (L2) 分类）统计，"
        f"{cn_day(day)} Layer 2 代币总市值出现超阈值变化。"
        f"触发原因：{'；'.join(reasons)}。"
        f"当日总市值约 {fmt_yi(mcap)}。"
        f"本条为阈值异动提醒，不是例行日报；不宜与 CryptoRank 外电数字直接对表。"
    )
    if historical:
        found = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
        body += f"本条为历史回扫发现（数据日 {day}，发现时间 {found} UTC+8），请勿当成今日刚发生。"
    body += f"\n{DASHBOARD_URL}"
    return f"{title}\n{body}"


def with_mark(mark: str, text: str) -> str:
    return f"{mark}\n{text}"


def mark_for(brief: dict[str, Any]) -> str:
    if brief["kind"] == "daily":
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
        return {"sentIds": [], "updatedAt": ""}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": [], "updatedAt": ""}
    ids = doc.get("sentIds") if isinstance(doc, dict) else None
    return {
        "sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else [],
        "updatedAt": str((doc or {}).get("updatedAt") or "") if isinstance(doc, dict) else "",
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
    parser.add_argument("--no-daily", action="store_true")
    args = parser.parse_args()
    if getattr(args, "history_scan", False):
        args.history_scan = False

    env = merge_env()
    dod_line = env_float(env, "L2_TOKEN_MCAP_DOD_PCT", DEFAULT_DOD_PCT)

    try:
        json_path = resolve_json_path(args.json or None)
        doc = load_doc(json_path)
        rows = rows_of(doc)
        if not rows:
            raise RuntimeError("data 为空")
    except (OSError, RuntimeError, FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    latest = str(rows[-1]["time"])
    print(
        f"[l2-token-mcap] json={json_path} latest={latest} rows={len(rows)} "
        f"mcap={rows[-1].get('market_cap')} dod_line={dod_line:g}%"
    )

    briefs: list[dict[str, Any]] = []
    hit, reasons, special = evaluate_threshold(rows, latest, dod_line=dod_line)
    if hit:
        briefs.append(
            {
                "id": f"l2-token-mcap-threshold-{latest}",
                "kind": "threshold",
                "historical": False,
                "special": special,
                "text": build_threshold_text(
                    rows, latest, reasons, historical=False, special=special
                ),
            }
        )

    if not args.no_daily:
        briefs.append(
            {
                "id": f"l2-token-mcap-daily-{latest}",
                "kind": "daily",
                "historical": False,
                "special": False,
                "text": build_daily_text(doc, rows, latest),
            }
        )

    state = load_state()
    sent_set = set(state.get("sentIds") or [])

    if args.history_scan:
        days = [str(r["time"]) for r in rows][-HISTORY_SCAN_DAYS:]
        hist_n = 0
        limit = max(0, int(args.history_limit))
        for day in reversed(days):
            if day == latest or hist_n >= limit:
                continue
            ok, why, sp = evaluate_threshold(rows, day, dod_line=dod_line)
            if not ok:
                continue
            msg_id = f"l2-token-mcap-threshold-{day}"
            if msg_id in sent_set and not args.force:
                continue
            briefs.append(
                {
                    "id": msg_id,
                    "kind": "threshold",
                    "historical": True,
                    "special": sp,
                    "text": build_threshold_text(
                        rows, day, why, historical=True, special=sp
                    ),
                }
            )
            hist_n += 1

    order = {"threshold": 0, "daily": 1}
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for brief in sorted(briefs, key=lambda b: (order.get(b["kind"], 9), b["id"])):
        if brief["id"] in seen:
            continue
        seen.add(brief["id"])
        unique.append(brief)

    for brief in unique:
        text = with_mark(mark_for(brief), brief["text"])
        already = brief["id"] in sent_set
        print("---")
        print(
            f"id={brief['id']} kind={brief['kind']} historical={brief['historical']} "
            f"special={brief.get('special')} already={already}"
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
            print(f"[skip] already {brief['id']}")
            continue
        text = with_mark(mark_for(brief), brief["text"])
        try:
            send_lark_text(webhook, secret, text)
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] Lark 失败 {brief['id']}: {exc}", file=sys.stderr)
            append_log(stamp, brief["id"], False, text, extra=f"err={exc}")
            return 1
        print(f"[pushed] {brief['id']}")
        if brief["id"] not in state["sentIds"]:
            state["sentIds"].append(brief["id"])
        sent_any = True
        append_log(stamp, brief["id"], True, text)
        time.sleep(0.4)

    if sent_any:
        state["sentIds"] = state["sentIds"][-160:]
        save_state(state)
        print(f"[l2-token-mcap] state → {STATE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
