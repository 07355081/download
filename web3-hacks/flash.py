# -*- coding: utf-8 -*-
"""DefiLlama Web3 hacks 月度损失 → Lark。

口径：DefiLlama /hacks 按 UTC 自然月汇总，不是 GoPlus 社区统计。

- 日常：每个刚闭合的自然月发一条【实时日常报告】
- 阈值：【历史阈值警报】——完整月损失创新高、月环比≥线、单笔最大≥线
- --history-scan：回扫未推送的阈值条

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
    HERE / "output" / "json" / "web3_hacks.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "web3-hacks" / "web3_hacks.json",
    Path("/root/dashboard/public/json/web3-hacks/web3_hacks.json"),
]
STATE_PATH = HERE / "output" / "web3_hacks_lark.json"
LOG_PATH = HERE / "flash.log"

ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

TZ_CN = timezone(timedelta(hours=8))
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/lark-feeds/web3-hacks"

DEFAULT_MOM_PCT = 100.0  # 月损失环比 |%|
DEFAULT_SINGLE_USD = 100_000_000.0  # 单笔最大 ≥ 1 亿美元
HISTORY_SCAN_MONTHS = 24
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
        "WEB3_HACKS_MOM_PCT",
        "WEB3_HACKS_SINGLE_USD",
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
    raise FileNotFoundError("找不到 web3_hacks.json，请先跑 download.py")


def load_months(path: Path) -> list[dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = doc.get("months") if isinstance(doc, dict) else None
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"{path} 无 months")
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        month = str(row.get("month") or "")
        if len(month) != 7:
            continue
        out.append(
            {
                "month": month,
                "count": int(row.get("count") or 0),
                "loss_usd": float(row.get("loss_usd") or 0),
                "top_name": row.get("top_name"),
                "top_loss_usd": float(row["top_loss_usd"]) if row.get("top_loss_usd") is not None else None,
                "top_day": row.get("top_day"),
                "complete": bool(row.get("complete")),
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
    if usd >= 1e8:
        return f"{usd / 1e8:.2f} 亿美元"
    return f"{usd / 1e4:,.0f} 万美元"


def fmt_pct_chg(pct: float) -> str:
    if pct >= 0:
        return f"增长 {pct:.1f}%"
    return f"下降 {abs(pct):.1f}%"


def mom_pct(curr: float, prev: float) -> float | None:
    if prev <= 0:
        return None
    return (curr / prev - 1.0) * 100.0


def evaluate_threshold(
    months: list[dict[str, Any]],
    month: str,
    *,
    mom_line: float,
    single_line: float,
) -> tuple[bool, list[str]]:
    done = complete_months(months)
    curr = next((m for m in done if m["month"] == month), None)
    if not curr:
        return False, []
    reasons: list[str] = []
    prev = prior_month(months, month)

    earlier = [m["loss_usd"] for m in done if m["month"] < month]
    if earlier and curr["loss_usd"] > max(earlier):
        reasons.append(f"{month} 月合计损失 {fmt_yi(curr['loss_usd'])} 创完整月新高")

    if prev:
        pct = mom_pct(curr["loss_usd"], prev["loss_usd"])
        if pct is not None and abs(pct) >= mom_line:
            reasons.append(
                f"相对 {prev['month']} 月损失环比 {pct:+.1f}%（线 ±{mom_line:g}%）"
            )

    top = curr.get("top_loss_usd")
    if isinstance(top, (int, float)) and float(top) >= single_line:
        name = curr.get("top_name") or "—"
        reasons.append(
            f"单笔最大 {name} 约 {fmt_yi(float(top))}（线 {fmt_yi(single_line)}）"
        )

    return bool(reasons), reasons


def build_daily_text(months: list[dict[str, Any]], month: str) -> str:
    curr = next(m for m in months if m["month"] == month)
    prev = prior_month(months, month)
    y, mo = int(month[:4]), int(month[5:7])
    prev_bit = ""
    if prev:
        pct = mom_pct(curr["loss_usd"], prev["loss_usd"])
        if pct is not None:
            # 倍数叙述（贴近社区稿习惯）
            if prev["loss_usd"] > 0:
                mult = curr["loss_usd"] / prev["loss_usd"]
                prev_bit = (
                    f"约为 {int(prev['month'][5:7])} 月（约 {fmt_yi(prev['loss_usd'])}）的 "
                    f"{mult:.1f} 倍，月环比{fmt_pct_chg(pct)}。"
                )
            else:
                prev_bit = f"月环比{fmt_pct_chg(pct)}。"
        # 再补一个月前对照（若有）
        older = prior_month(months, prev["month"])
        if older and older["loss_usd"] > 0:
            mult2 = curr["loss_usd"] / older["loss_usd"]
            prev_bit += (
                f"约为 {int(older['month'][5:7])} 月（约 {fmt_yi(older['loss_usd'])}）的 "
                f"{mult2:.1f} 倍。"
            )

    top_bit = ""
    if curr.get("top_name") and curr.get("top_loss_usd") is not None:
        top_bit = (
            f"单笔最大损失来自 {curr['top_name']}，约 {fmt_yi(float(curr['top_loss_usd']))}。"
        )

    title = (
        f"Web3 安全事件：{y} 年 {mo} 月约 {curr['count']} 起，"
        f"合计损失约 {fmt_yi(curr['loss_usd'])}"
    )
    body = (
        f"据吴说数据中心统计（DefiLlama Hacks 口径），{y} 年 {mo} 月共记录 "
        f"{curr['count']} 起 Web3 安全事件，合计损失约 {fmt_yi(curr['loss_usd'])}"
        f"（{curr['loss_usd']:,.0f} 美元）。"
        f"{prev_bit}{top_bit}"
        f"本条为刚闭合自然月例行摘要；口径为 DefiLlama，与 GoPlus 等社区「重大事件」次数/金额可能不一致。"
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
    title = f"Web3 安全损失异动：{y} 年 {mo} 月触发阈值"
    body = (
        f"吴说获悉，DefiLlama Hacks 统计的 Web3 安全损失在 {y} 年 {mo} 月出现超阈值变化。"
        f"触发原因：{'；'.join(reasons)}。"
        f"当月共 {curr['count']} 起，合计损失约 {fmt_yi(curr['loss_usd'])}。"
        f"本条为阈值异动提醒，不是例行月报；口径非 GoPlus 社区统计。"
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
    parser.add_argument("--no-daily", action="store_true")
    args = parser.parse_args()
    if getattr(args, "history_scan", False):
        args.history_scan = False

    env = merge_env()
    mom_line = env_float(env, "WEB3_HACKS_MOM_PCT", DEFAULT_MOM_PCT)
    single_line = env_float(env, "WEB3_HACKS_SINGLE_USD", DEFAULT_SINGLE_USD)

    try:
        json_path = resolve_json_path(args.json or None)
        months = load_months(json_path)
    except (OSError, RuntimeError, FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    latest = newest_complete(months)
    if not latest:
        print("[ERROR] 尚无完整自然月", file=sys.stderr)
        return 1
    latest_m = latest["month"]
    print(
        f"[web3-hacks] json={json_path} latest_complete={latest_m} "
        f"count={latest['count']} loss={latest['loss_usd']:,.0f} "
        f"mom_line={mom_line:g}% single≥{single_line:g}"
    )

    briefs: list[dict[str, Any]] = []

    hit, reasons = evaluate_threshold(
        months, latest_m, mom_line=mom_line, single_line=single_line
    )
    if hit:
        briefs.append(
            {
                "id": f"web3-hacks-threshold-{latest_m}",
                "kind": "threshold",
                "historical": False,
                "text": build_threshold_text(months, latest_m, reasons, historical=False),
            }
        )

    if not args.no_daily:
        briefs.append(
            {
                "id": f"web3-hacks-daily-{latest_m}",
                "kind": "daily",
                "historical": False,
                "text": build_daily_text(months, latest_m),
            }
        )

    state = load_state()
    sent_set = set(state.get("sentIds") or [])

    if args.history_scan:
        done = complete_months(months)
        scan = done[-HISTORY_SCAN_MONTHS:]
        hist_n = 0
        limit = max(0, int(args.history_limit))
        for row in reversed(scan):
            month = row["month"]
            if month == latest_m or hist_n >= limit:
                continue
            msg_id = f"web3-hacks-threshold-{month}"
            if msg_id in sent_set and not args.force:
                continue
            ok, why = evaluate_threshold(
                months, month, mom_line=mom_line, single_line=single_line
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

    order = {"threshold": 0, "daily": 1}
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for brief in sorted(briefs, key=lambda b: (order.get(b["kind"], 9), b["id"])):
        if brief["id"] in seen:
            continue
        seen.add(brief["id"])
        unique.append(brief)

    for brief in unique:
        mark = LARK_MARK_THRESHOLD if brief["kind"] == "threshold" else LARK_MARK_DAILY
        text = with_mark(mark, brief["text"])
        already = brief["id"] in sent_set
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
        return 1

    ok_all = True
    for brief in unique:
        if brief["id"] in sent_set and not args.force:
            print(f"[skip] already {brief['id']}")
            continue
        mark = LARK_MARK_THRESHOLD if brief["kind"] == "threshold" else LARK_MARK_DAILY
        text = with_mark(mark, brief["text"])
        stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
        try:
            send_lark_text(webhook, secret, text)
            append_log(stamp, brief["id"], True, text)
            sent_set.add(brief["id"])
            print(f"[pushed] {brief['id']}")
            time.sleep(0.5)
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            append_log(stamp, brief["id"], False, text, extra=str(exc))
            print(f"[ERROR] {brief['id']}: {exc}", file=sys.stderr)
            ok_all = False

    state["sentIds"] = sorted(sent_set)[-500:]
    save_state(state)
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
