# -*- coding: utf-8 -*-
"""全球/分链 DEX 现货成交 → Lark。

口径：DefiLlama dailyVolume，不是 CryptoRank。数字可能与外电不一致。

- 日常：【实时日常报告】最新完整日全球成交 + Top 链
- 阈值：【历史阈值警报】全球/主力链日环比刚越过阈值
- 特殊：【季度特殊警报】完整季末日：连跌后回升、首次年内季环比转正、全球/链季环比极端

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
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    HERE / "output" / "json" / "dex_volume.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "dex-volume" / "dex_volume.json",
    Path("/root/dashboard/public/json/dex-volume/dex_volume.json"),
]
STATE_PATH = HERE / "output" / "dex_volume_lark.json"
LOG_PATH = HERE / "dex_volume_flash.log"

ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

TZ_CN = timezone(timedelta(hours=8))
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
LARK_MARK_SPECIAL = "【季度特殊警报】"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/lark-feeds/dex-volume"

DEFAULT_GLOBAL_DOD_PCT = 15.0
DEFAULT_CHAIN_DOD_PCT = 25.0
DEFAULT_GLOBAL_QOQ_PCT = 20.0
DEFAULT_CHAIN_QOQ_PCT = 50.0
HISTORY_SCAN_DAYS = 60
DEFAULT_HISTORY_LIMIT = 4

WATCH_KEYS = (
    "robinhood",
    "solana",
    "ethereum",
    "base",
    "bsc",
    "arbitrum",
    "hyperliquid_l1",
)
LABELS = {
    "global": "全球",
    "robinhood": "Robinhood Chain",
    "solana": "Solana",
    "ethereum": "Ethereum",
    "base": "Base",
    "bsc": "BNB Chain",
    "arbitrum": "Arbitrum",
    "hyperliquid_l1": "Hyperliquid L1",
}


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
        "DEX_VOLUME_GLOBAL_DOD_PCT",
        "DEX_VOLUME_CHAIN_DOD_PCT",
        "DEX_VOLUME_GLOBAL_QOQ_PCT",
        "DEX_VOLUME_CHAIN_QOQ_PCT",
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


def resolve_json_path(override: str | None) -> Path:
    if override:
        path = Path(override)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("dex_volume.json not found; run download.py first")


def load_doc(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("empty data")
    clean = [r for r in rows if isinstance(r, dict) and r.get("time")]
    latest_chains = doc.get("latest_chains") if isinstance(doc, dict) else []
    quarters = doc.get("quarters") if isinstance(doc, dict) else []
    if not isinstance(latest_chains, list):
        latest_chains = []
    if not isinstance(quarters, list):
        quarters = []
    return clean, latest_chains, quarters


def pct_change(curr: float, prev: float) -> float | None:
    if prev == 0:
        return None
    return (curr / prev - 1.0) * 100.0


def fmt_yi(v: float) -> str:
    if abs(v) >= 1e8:
        return f"{v / 1e8:.2f} 亿美元"
    return f"{v / 1e4:,.0f} 万美元"


def fmt_pct(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.1f}%"


def row_by_day(rows: list[dict[str, Any]], day: str) -> dict[str, Any] | None:
    return next((r for r in rows if str(r.get("time")) == day), None)


def evaluate_daily_threshold(
    rows: list[dict[str, Any]],
    day: str,
    *,
    global_line: float,
    chain_line: float,
) -> tuple[bool, list[str]]:
    idx = next((i for i, r in enumerate(rows) if str(r["time"]) == day), None)
    if idx is None or idx == 0:
        return False, []
    curr, prev = rows[idx], rows[idx - 1]
    reasons: list[str] = []

    g0, g1 = prev.get("global"), curr.get("global")
    if isinstance(g0, (int, float)) and isinstance(g1, (int, float)):
        dod = pct_change(float(g1), float(g0))
        if dod is not None and abs(dod) >= global_line:
            crossed = True
            if idx >= 2:
                g_prev2 = rows[idx - 2].get("global")
                prev_dod = (
                    pct_change(float(g0), float(g_prev2))
                    if isinstance(g_prev2, (int, float))
                    else None
                )
                if prev_dod is not None and abs(prev_dod) >= global_line and (prev_dod > 0) == (dod > 0):
                    crossed = False
            if crossed:
                reasons.append(
                    f"全球 DEX 成交日环比 {fmt_pct(dod)}（现约 {fmt_yi(float(g1))}，线 ±{global_line:g}%）"
                )

    for key in WATCH_KEYS:
        a, b = curr.get(key), prev.get(key)
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            continue
        dod = pct_change(float(a), float(b))
        if dod is None or abs(dod) < chain_line:
            continue
        if idx >= 2:
            c = rows[idx - 2].get(key)
            prev_dod = pct_change(float(b), float(c)) if isinstance(c, (int, float)) else None
            if prev_dod is not None and abs(prev_dod) >= chain_line and (prev_dod > 0) == (dod > 0):
                continue
        reasons.append(
            f"{LABELS.get(key, key)} 日环比 {fmt_pct(dod)}（现约 {fmt_yi(float(a))}，线 ±{chain_line:g}%）"
        )
    return bool(reasons), reasons


def evaluate_special(
    quarters: list[dict[str, Any]],
    chain_quarters: dict[str, list[dict[str, Any]]],
    as_of: str,
    *,
    global_qoq_line: float,
    chain_qoq_line: float,
) -> tuple[bool, list[str], str | None]:
    day = date.fromisoformat(as_of)
    if day.month not in (3, 6, 9, 12) or day.day != monthrange(day.year, day.month)[1]:
        return False, [], None

    q_n = day.month // 3
    label = f"{day.year}-Q{q_n}"
    reasons: list[str] = []
    suffix: str | None = None

    qs = [q for q in quarters if isinstance(q, dict) and q.get("quarter")]
    idx = next((i for i, q in enumerate(qs) if q["quarter"] == label), None)
    if idx is not None:
        cur = qs[idx]
        qoq = cur.get("qoq_pct")
        vol = float(cur.get("volume_usd") or 0)
        if isinstance(qoq, (int, float)):
            if idx >= 3:
                prev3 = qs[idx - 3 : idx]
                if qoq > 0 and all(
                    isinstance(p.get("qoq_pct"), (int, float)) and float(p["qoq_pct"]) < 0 for p in prev3
                ):
                    reasons.append(
                        f"{label} 全球 DEX 成交约 {fmt_yi(vol)}，季环比 {fmt_pct(float(qoq))}，"
                        f"此前连续三个季度下降后回升"
                    )
                    suffix = f"qoq-rebound-{label}"
            # 年内首次季环比转正（当年 Q1 起）
            year_qs = [q for q in qs if str(q["quarter"]).startswith(f"{day.year}-")]
            yi = next((i for i, q in enumerate(year_qs) if q["quarter"] == label), None)
            if (
                yi is not None
                and isinstance(qoq, (int, float))
                and qoq > 0
                and all(
                    not isinstance(q.get("qoq_pct"), (int, float)) or float(q["qoq_pct"]) <= 0
                    for q in year_qs[:yi]
                )
            ):
                reasons.append(
                    f"{label} 全球 DEX 成交约 {fmt_yi(vol)}，季环比 {fmt_pct(float(qoq))}，"
                    f"为 {day.year} 年迄今首次季度环比增长"
                )
                suffix = suffix or f"qoq-first-up-{label}"
            if abs(float(qoq)) >= global_qoq_line:
                reasons.append(
                    f"{label} 全球 DEX 成交约 {fmt_yi(vol)}，季环比 {fmt_pct(float(qoq))}"
                    f"（|Δ|≥{global_qoq_line:g}%）"
                )
                suffix = suffix or f"qoq-global-{label}"

    for key in WATCH_KEYS:
        cqs = [q for q in (chain_quarters.get(key) or []) if isinstance(q, dict)]
        cur = next((q for q in cqs if q.get("quarter") == label), None)
        if not cur:
            continue
        qoq = cur.get("qoq_pct")
        vol = float(cur.get("volume_usd") or 0)
        if not isinstance(qoq, (int, float)) or float(qoq) < chain_qoq_line:
            continue
        reasons.append(
            f"{LABELS.get(key, key)} {label} 成交约 {fmt_yi(vol)}，季环比 {fmt_pct(float(qoq))}"
            f"（线 {chain_qoq_line:g}%）"
        )
        suffix = suffix or f"qoq-chain-{key}-{label}"

    # de-dupe reason text while keeping order
    seen: set[str] = set()
    uniq: list[str] = []
    for r in reasons:
        if r in seen:
            continue
        seen.add(r)
        uniq.append(r)
    return bool(uniq), uniq, suffix


def build_daily_text(
    rows: list[dict[str, Any]], day: str, latest_chains: list[dict[str, Any]]
) -> str:
    curr = row_by_day(rows, day)
    assert curr is not None
    total = float(curr.get("global") or 0)
    y, m, d = day.split("-")
    top_bits: list[str] = []
    if latest_chains:
        for item in latest_chains[:5]:
            name = str(item.get("label") or item.get("key") or "")
            vol = float(item.get("volume_usd") or 0)
            top_bits.append(f"{name} 约 {fmt_yi(vol)}")
    else:
        for key in WATCH_KEYS[:5]:
            val = curr.get(key)
            if isinstance(val, (int, float)):
                top_bits.append(f"{LABELS.get(key, key)} 约 {fmt_yi(float(val))}")
    top_s = "；".join(top_bits) if top_bits else "—"
    dod = ""
    idx = next(i for i, r in enumerate(rows) if str(r["time"]) == day)
    if idx > 0:
        prev = rows[idx - 1].get("global")
        if isinstance(prev, (int, float)) and prev > 0:
            dod = f"，较前一完整日 {fmt_pct(pct_change(total, float(prev)) or 0)}"
    title = f"DEX 现货成交：全球约 {fmt_yi(total)}"
    body = (
        f"据吴说数据中心统计，截至 {int(y)} 年 {int(m)} 月 {int(d)} 日，"
        f"全球 DEX 现货成交约 {fmt_yi(total)}{dod}。"
        f"分链靠前约：{top_s}。"
        f"口径为 DefiLlama（dailyVolume），与 CryptoRank 等第三方数字可能不一致。"
        f"\n{DASHBOARD_URL}"
    )
    return f"{title}\n{body}"


def build_threshold_text(
    rows: list[dict[str, Any]], day: str, reasons: list[str], *, historical: bool
) -> str:
    curr = row_by_day(rows, day) or {}
    total = float(curr.get("global") or 0)
    y, m, d = day.split("-")
    title = f"DEX 现货成交异动：{int(m)} 月 {int(d)} 日触发阈值"
    body = (
        f"吴说获悉，DefiLlama DEX 现货成交在 {int(y)} 年 {int(m)} 月 {int(d)} 日出现超阈值变化。"
        f"触发原因：{'；'.join(reasons)}。"
        f"当日全球成交约 {fmt_yi(total)}。"
        f"本条为阈值异动提醒，不是例行日报。"
    )
    if historical:
        found = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
        body += f"本条为历史回扫发现（数据日 {day}，发现时间 {found} UTC+8），请勿当成今日刚发生。"
    body += f"\n{DASHBOARD_URL}"
    return f"{title}\n{body}"


def build_special_text(
    rows: list[dict[str, Any]], day: str, reasons: list[str], *, historical: bool
) -> str:
    curr = row_by_day(rows, day) or {}
    total = float(curr.get("global") or 0)
    y, m, d = day.split("-")
    title = f"DEX 现货成交季度异动：截至 {int(y)} 年 {int(m)} 月"
    body = (
        f"据吴说数据中心统计，截至 {int(y)} 年 {int(m)} 月 {int(d)} 日出现需单独提示的季度变化。"
        f"{'；'.join(reasons)}。"
        f"当日全球成交约 {fmt_yi(total)}。"
        f"本条为【季度特殊警报】，与日常阈值分开；口径为 DefiLlama，不宜与 CryptoRank 外电数字直接对表。"
    )
    if historical:
        found = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
        body += f"本条为历史回扫发现（数据日 {day}，发现时间 {found} UTC+8）。"
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
    global_line = env_float(env, "DEX_VOLUME_GLOBAL_DOD_PCT", DEFAULT_GLOBAL_DOD_PCT)
    chain_line = env_float(env, "DEX_VOLUME_CHAIN_DOD_PCT", DEFAULT_CHAIN_DOD_PCT)
    global_qoq = env_float(env, "DEX_VOLUME_GLOBAL_QOQ_PCT", DEFAULT_GLOBAL_QOQ_PCT)
    chain_qoq = env_float(env, "DEX_VOLUME_CHAIN_QOQ_PCT", DEFAULT_CHAIN_QOQ_PCT)

    try:
        json_path = resolve_json_path(args.json or None)
        doc = json.loads(json_path.read_text(encoding="utf-8"))
        rows, latest_chains, quarters = load_doc(json_path)
        chain_quarters = doc.get("chain_quarters") if isinstance(doc, dict) else {}
        if not isinstance(chain_quarters, dict):
            chain_quarters = {}
    except (OSError, RuntimeError, FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    latest = str(rows[-1]["time"])
    print(
        f"[dex-volume] json={json_path} latest={latest} rows={len(rows)} "
        f"global_dod={global_line:g}% chain_dod={chain_line:g}% "
        f"global_qoq={global_qoq:g}% chain_qoq={chain_qoq:g}%"
    )

    briefs: list[dict[str, Any]] = []

    hit_s, why_s, suf = evaluate_special(
        quarters,
        {str(k): v for k, v in chain_quarters.items() if isinstance(v, list)},
        latest,
        global_qoq_line=global_qoq,
        chain_qoq_line=chain_qoq,
    )
    if hit_s and suf:
        briefs.append(
            {
                "id": f"dex-volume-special-{suf}",
                "kind": "special",
                "historical": False,
                "text": build_special_text(rows, latest, why_s, historical=False),
            }
        )

    hit_t, why_t = evaluate_daily_threshold(
        rows, latest, global_line=global_line, chain_line=chain_line
    )
    if hit_t:
        briefs.append(
            {
                "id": f"dex-volume-threshold-{latest}",
                "kind": "threshold",
                "historical": False,
                "text": build_threshold_text(rows, latest, why_t, historical=False),
            }
        )

    if not args.no_daily:
        briefs.append(
            {
                "id": f"dex-volume-daily-{latest}",
                "kind": "daily",
                "historical": False,
                "text": build_daily_text(rows, latest, latest_chains),
            }
        )

    state = load_state()
    sent_set = set(state.get("sentIds") or [])

    if args.history_scan:
        days = [str(r["time"]) for r in rows]
        scan_days = days[-HISTORY_SCAN_DAYS:]
        hist_n = 0
        limit = max(0, int(args.history_limit))
        cq = {str(k): v for k, v in chain_quarters.items() if isinstance(v, list)}
        # 先扫季度特殊，避免被密集日阈值占满 history-limit
        for day in reversed(scan_days):
            if day == latest or hist_n >= limit:
                continue
            ok_s, why, suf = evaluate_special(
                quarters, cq, day, global_qoq_line=global_qoq, chain_qoq_line=chain_qoq
            )
            if not (ok_s and suf):
                continue
            msg_id = f"dex-volume-special-{suf}"
            if msg_id in sent_set and not args.force:
                continue
            briefs.append(
                {
                    "id": msg_id,
                    "kind": "special",
                    "historical": True,
                    "text": build_special_text(rows, day, why, historical=True),
                }
            )
            hist_n += 1
        for day in reversed(scan_days):
            if day == latest or hist_n >= limit:
                continue
            ok_t, why_t = evaluate_daily_threshold(
                rows, day, global_line=global_line, chain_line=chain_line
            )
            if not ok_t:
                continue
            msg_id = f"dex-volume-threshold-{day}"
            if msg_id in sent_set and not args.force:
                continue
            briefs.append(
                {
                    "id": msg_id,
                    "kind": "threshold",
                    "historical": True,
                    "text": build_threshold_text(rows, day, why_t, historical=True),
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
        print(f"[dex-volume] state → {STATE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
