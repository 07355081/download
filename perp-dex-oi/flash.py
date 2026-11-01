# -*- coding: utf-8 -*-
"""Perp DEX 未平仓（DefiLlama 衍生品一类）→ Lark。

- 日常：【实时日常报告】最新总未平仓 + Hyperliquid / Aster / Variational
- 阈值：【历史阈值警报】总未平仓周环比、Hyperliquid 占比日变过线，或创已存历史高点
- 创历史高点时同一条再加【特殊警报】
- 不回扫旧日期。--history-scan 保留参数但不再发历史。

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
    HERE / "output" / "json" / "perp_dex_oi.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "perp-dex-oi" / "perp_dex_oi.json",
    Path("/root/dashboard/public/json/perp-dex-oi/perp_dex_oi.json"),
]
STATE_PATH = HERE / "output" / "perp_dex_oi_lark.json"
LOG_PATH = HERE / "perp_dex_oi_flash.log"

ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

TZ_CN = timezone(timedelta(hours=8))
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
LARK_MARK_SPECIAL = "【特殊警报】"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/lark-feeds/perp-dex-oi"

DEFAULT_HL_SHARE_PP = 3.0  # HL 份额日变 |百分点|
DEFAULT_TOTAL_WOW_PCT = 20.0  # 总 OI 周环比 |%|
MIN_PRIOR_DAYS = 30  # 创历史高点至少要有这么多更早的日子，避免序列开头误报

FOCUS_ORDER = ("hyperliquid_perps", "aster_perps", "variational")


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
        "PERP_DEX_OI_HL_SHARE_PP",
        "PERP_DEX_OI_TOTAL_WOW_PCT",
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
    raise FileNotFoundError("找不到 perp_dex_oi.json，请先跑 download.py")


def load_doc(path: Path) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise RuntimeError("invalid json root")
    history = doc.get("history")
    if not isinstance(history, list) or not history:
        raise RuntimeError("无 history")
    return doc


def fmt_yi(usd: float) -> str:
    yi = usd / 1e8
    if abs(yi) >= 100:
        return f"{yi:.1f} 亿美元"
    return f"{yi:.2f} 亿美元"


def fmt_pct(pct: float) -> str:
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def pct_change(curr: float, prev: float) -> float | None:
    if prev <= 0:
        return None
    return (curr - prev) / prev * 100.0


def hist_map(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in doc.get("history") or []:
        if not isinstance(row, dict):
            continue
        day = str(row.get("time") or "")[:10]
        if len(day) == 10:
            out[day] = row
    return out


def focus_from_hist(row: dict[str, Any]) -> dict[str, float]:
    return {k: float(row.get(k) or 0) for k in FOCUS_ORDER}


def share_of(oi: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return oi / total * 100.0


def cn_day(day: str) -> str:
    y, m, d = day.split("-")
    return f"{int(m)} 月 {int(d)} 日"


def build_core_sentence(doc: dict[str, Any], day: str | None = None) -> str:
    """通用正文核心句。"""
    as_of = day or str(doc.get("as_of") or "")
    hmap = hist_map(doc)
    row = hmap.get(as_of)
    if row:
        total = float(row.get("total_oi") or 0)
        focus_oi = focus_from_hist(row)
    else:
        total = float(doc.get("total_oi") or 0)
        focus_oi = {}
        for item in doc.get("focus") or []:
            if isinstance(item, dict) and item.get("key") in FOCUS_ORDER:
                focus_oi[str(item["key"])] = float(item.get("oi") or 0)

    parts_amt = []
    parts_share = []
    for key in FOCUS_ORDER:
        oi = float(focus_oi.get(key) or 0)
        parts_amt.append(fmt_yi(oi))
        parts_share.append(f"{share_of(oi, total):.1f}%")

    return (
        f"据吴说数据中心统计，截至 {cn_day(as_of)}，"
        f"链上永续 DEX（按 DefiLlama 衍生品一类）未平仓合计约 {fmt_yi(total)}；"
        f"其中 Hyperliquid 约 {parts_amt[0]}，占 {parts_share[0]}；"
        f"Aster 约 {parts_amt[1]}，占 {parts_share[1]}；"
        f"Variational 约 {parts_amt[2]}，占 {parts_share[2]}。"
        f"\n{DASHBOARD_URL}"
    )


def with_mark(mark: str, text: str) -> str:
    if text.startswith(mark):
        return text
    return f"{mark}\n{text}"


def prev_day(day: str) -> str | None:
    try:
        d = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (d - timedelta(days=1)).isoformat()


def day_n_ago(day: str, n: int) -> str | None:
    try:
        d = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (d - timedelta(days=n)).isoformat()


def evaluate_high(doc: dict[str, Any], day: str) -> tuple[bool, str]:
    """总未平仓或 Hyperliquid 占比高于此前每一天。"""
    days = sorted(hist_map(doc))
    if day not in days:
        return False, ""
    idx = days.index(day)
    if idx < MIN_PRIOR_DAYS:
        return False, ""
    hmap = hist_map(doc)
    cur = hmap[day]
    total = float(cur.get("total_oi") or 0)
    hl_share = share_of(float(cur.get("hyperliquid_perps") or 0), total)
    prior_totals: list[float] = []
    prior_shares: list[float] = []
    for prev_day_key in days[:idx]:
        prev = hmap[prev_day_key]
        prev_total = float(prev.get("total_oi") or 0)
        if prev_total <= 0:
            continue
        prior_totals.append(prev_total)
        prior_shares.append(share_of(float(prev.get("hyperliquid_perps") or 0), prev_total))
    reasons: list[str] = []
    if prior_totals and total > max(prior_totals):
        reasons.append(f"总未平仓 {fmt_yi(total)} 高于已存历史高点 {fmt_yi(max(prior_totals))}")
    if prior_shares and hl_share > max(prior_shares):
        reasons.append(
            f"Hyperliquid 占比 {hl_share:.1f}% 高于已存历史高点 {max(prior_shares):.1f}%"
        )
    return bool(reasons), "；".join(reasons)


def evaluate_threshold(
    doc: dict[str, Any],
    day: str,
    *,
    hl_share_pp: float,
    total_wow: float,
) -> tuple[bool, str, bool]:
    """返回 (过线, 原因, 是否创历史高点)。"""
    hmap = hist_map(doc)
    cur = hmap.get(day)
    if not cur:
        return False, "无当日历史行", False
    reasons: list[str] = []
    total = float(cur.get("total_oi") or 0)
    hl = float(cur.get("hyperliquid_perps") or 0)
    hl_share = share_of(hl, total)

    pd = prev_day(day)
    if pd and pd in hmap:
        prev = hmap[pd]
        prev_total = float(prev.get("total_oi") or 0)
        prev_hl_share = share_of(float(prev.get("hyperliquid_perps") or 0), prev_total)
        delta_pp = hl_share - prev_hl_share
        if abs(delta_pp) >= hl_share_pp:
            reasons.append(
                f"Hyperliquid 占比日变 {delta_pp:+.1f} 个百分点（线 {hl_share_pp:g} 个百分点）"
            )

    wday = day_n_ago(day, 7)
    if wday and wday in hmap:
        wow = pct_change(total, float(hmap[wday].get("total_oi") or 0))
        if wow is not None and abs(wow) >= total_wow:
            reasons.append(f"总未平仓周环比 {fmt_pct(wow)}（线 {total_wow:g}%）")

    high, high_why = evaluate_high(doc, day)
    if high:
        reasons.append(high_why)

    return bool(reasons), "；".join(reasons) if reasons else "未过线", high


def build_daily_text(doc: dict[str, Any]) -> str:
    return build_core_sentence(doc, day=str(doc.get("as_of") or ""))


def build_threshold_text(doc: dict[str, Any], day: str, why: str, *, record: bool) -> str:
    head = "这是历史高点，不是日常新闻。" if record else ""
    return f"{head}链上永续 DEX 未平仓出现超阈值变动：{why}。" + build_core_sentence(doc, day=day)


def gen_sign(secret: str, timestamp: int) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
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
        webhook,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
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
    if not isinstance(doc, dict):
        return {"sentIds": []}
    ids = doc.get("sentIds")
    if not isinstance(ids, list):
        ids = []
    return {"sentIds": [str(x) for x in ids]}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def append_log(stamp: str, msg_id: str, ok: bool, text: str, extra: str = "") -> None:
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} id={msg_id} ok={ok} {extra}\n{text}\n\n")


def mark_for(brief: dict[str, Any]) -> str:
    if brief.get("kind") == "threshold" and brief.get("record"):
        return f"{LARK_MARK_THRESHOLD}\n{LARK_MARK_SPECIAL}"
    if brief.get("kind") == "threshold":
        return LARK_MARK_THRESHOLD
    return LARK_MARK_DAILY


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--history-scan", action="store_true", help="已关闭，传入也不补发旧日期")
    parser.add_argument("--json", default="")
    parser.add_argument("--no-daily", action="store_true")
    args = parser.parse_args()
    if getattr(args, "history_scan", False):
        args.history_scan = False

    env = merge_env()
    hl_pp = env_float(env, "PERP_DEX_OI_HL_SHARE_PP", DEFAULT_HL_SHARE_PP)
    total_wow = env_float(env, "PERP_DEX_OI_TOTAL_WOW_PCT", DEFAULT_TOTAL_WOW_PCT)

    try:
        json_path = resolve_json_path(args.json or None)
        doc = load_doc(json_path)
    except (OSError, RuntimeError, FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    latest = str(doc.get("as_of") or "")
    print(
        f"[perp-dex-oi] json={json_path} as_of={latest} "
        f"hl_pp={hl_pp:g} wow={total_wow:g}%"
    )
    if args.history_scan:
        print("[perp-dex-oi] 已关闭历史回扫，不补发旧日期")

    briefs: list[dict[str, Any]] = []

    hit_t, why_t, record = evaluate_threshold(
        doc, latest, hl_share_pp=hl_pp, total_wow=total_wow
    )
    if hit_t:
        briefs.append(
            {
                "id": f"perp-dex-oi-threshold-{latest}",
                "kind": "threshold",
                "record": record,
                "historical": False,
                "text": build_threshold_text(doc, latest, why_t, record=record),
            }
        )

    if not args.no_daily:
        briefs.append(
            {
                "id": f"perp-dex-oi-daily-{latest}",
                "kind": "daily",
                "historical": False,
                "text": build_daily_text(doc),
            }
        )

    state = load_state()
    sent_set = set(state.get("sentIds") or [])

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
        text = with_mark(mark_for(brief), brief["text"])
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
        print(f"[perp-dex-oi] state → {STATE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
