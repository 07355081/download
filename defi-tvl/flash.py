# -*- coding: utf-8 -*-
"""全球/分链 DeFi TVL → Lark。

口径：DefiLlama（不含流动性质押与双计），不是 CryptoRank。数字可能与外电不一致。

- 日常：【实时日常报告】最新完整日全球 TVL + Top 链
- 阈值：【历史阈值警报】全球日环比或主力链日环比刚越过阈值
- 特殊：【季度特殊警报】季度回升/连跌后反转、连续≥3 完整月同向、单季链涨幅极端
- --history-scan：回扫未推送的阈值/特殊条，抬头标明历史回扫

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
    HERE / "output" / "json" / "defi_tvl.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "defi-tvl" / "defi_tvl.json",
    Path("/root/dashboard/public/json/defi-tvl/defi_tvl.json"),
]
STATE_PATH = HERE / "output" / "defi_tvl_lark.json"
LOG_PATH = HERE / "defi_tvl_flash.log"

ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

TZ_CN = timezone(timedelta(hours=8))
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
LARK_MARK_SPECIAL = "【季度特殊警报】"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/lark-feeds/defi-tvl"

DEFAULT_GLOBAL_DOD_PCT = 3.0  # 全球日环比 |%|
DEFAULT_CHAIN_DOD_PCT = 8.0  # 单链日环比 |%|
DEFAULT_CHAIN_QOQ_PCT = 80.0  # 单链完整季环比 %
HISTORY_SCAN_DAYS = 60
DEFAULT_HISTORY_LIMIT = 4

WATCH_KEYS = (
    "ethereum",
    "solana",
    "base",
    "bsc",
    "tron",
    "monad",
    "robinhood_chain",
    "ton",
)
LABELS = {
    "global": "全球",
    "ethereum": "Ethereum",
    "solana": "Solana",
    "base": "Base",
    "bsc": "BNB Chain",
    "tron": "TRON",
    "monad": "Monad",
    "robinhood_chain": "Robinhood Chain",
    "arbitrum": "Arbitrum",
    "hyperliquid_l1": "Hyperliquid L1",
    "avalanche": "Avalanche",
    "polygon": "Polygon",
    "ton": "TON",
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
        "DEFI_TVL_GLOBAL_DOD_PCT",
        "DEFI_TVL_CHAIN_DOD_PCT",
        "DEFI_TVL_CHAIN_QOQ_PCT",
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
    raise FileNotFoundError("找不到 defi_tvl.json，请先跑 download.py")


def load_doc(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    data = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"{path} 无 data")
    rows: list[dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        day = str(row.get("time") or "")[:10]
        if len(day) != 10:
            continue
        item: dict[str, Any] = {"time": day}
        for key, value in row.items():
            if key == "time":
                continue
            if isinstance(value, (int, float)):
                item[key] = float(value)
        rows.append(item)
    rows.sort(key=lambda r: str(r["time"]))
    latest = doc.get("latest_chains") if isinstance(doc, dict) else []
    if not isinstance(latest, list):
        latest = []
    return rows, latest


def fmt_yi(usd: float) -> str:
    yi = usd / 1e8
    if abs(yi) >= 100:
        return f"{yi:.1f} 亿美元"
    if abs(yi) >= 10:
        return f"{yi:.2f} 亿美元"
    return f"{yi:.2f} 亿美元"


def fmt_pct(pct: float) -> str:
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def row_by_day(rows: list[dict[str, Any]], day: str) -> dict[str, Any] | None:
    for row in rows:
        if str(row["time"]) == day:
            return row
    return None


def pct_change(curr: float, prev: float) -> float | None:
    if prev <= 0:
        return None
    return (curr - prev) / prev * 100.0


def month_end_tvl(rows: list[dict[str, Any]], year: int, month: int, key: str = "global") -> float | None:
    last_day = date(year, month, monthrange(year, month)[1]).isoformat()
    hit: float | None = None
    for row in rows:
        day = str(row["time"])
        if day > last_day:
            break
        if day[:7] == f"{year:04d}-{month:02d}":
            val = row.get(key)
            if isinstance(val, (int, float)):
                hit = float(val)
    return hit


def quarter_end_tvl(rows: list[dict[str, Any]], year: int, q: int, key: str = "global") -> float | None:
    month = q * 3
    return month_end_tvl(rows, year, month, key)


def complete_quarters(rows: list[dict[str, Any]], key: str = "global") -> list[tuple[str, float, float | None]]:
    """[(label like 2026Q3, tvl, qoq_pct), ...] only complete quarters."""
    if not rows:
        return []
    first = date.fromisoformat(str(rows[0]["time"]))
    last = date.fromisoformat(str(rows[-1]["time"]))
    out: list[tuple[str, float, float | None]] = []
    y, q = first.year, (first.month - 1) // 3 + 1
    prev_tvl: float | None = None
    while True:
        end_m = q * 3
        end_d = date(y, end_m, monthrange(y, end_m)[1])
        if end_d > last:
            break
        tvl = quarter_end_tvl(rows, y, q, key)
        if tvl is not None:
            qoq = pct_change(tvl, prev_tvl) if prev_tvl is not None else None
            out.append((f"{y}Q{q}", tvl, qoq))
            prev_tvl = tvl
        if q == 4:
            y, q = y + 1, 1
        else:
            q += 1
        if y > last.year + 1:
            break
    return out


def complete_months(rows: list[dict[str, Any]], key: str = "global") -> list[tuple[str, float, float | None]]:
    if not rows:
        return []
    first = date.fromisoformat(str(rows[0]["time"]))
    last = date.fromisoformat(str(rows[-1]["time"]))
    out: list[tuple[str, float, float | None]] = []
    y, m = first.year, first.month
    prev: float | None = None
    while True:
        end = date(y, m, monthrange(y, m)[1])
        if end > last:
            break
        tvl = month_end_tvl(rows, y, m, key)
        if tvl is not None:
            mom = pct_change(tvl, prev) if prev is not None else None
            out.append((f"{y}-{m:02d}", tvl, mom))
            prev = tvl
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
        if y > last.year + 1:
            break
    return out


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
            # 刚越过：前一日未破同向线
            if idx >= 2:
                g_prev2 = rows[idx - 2].get("global")
                prev_dod = (
                    pct_change(float(g0), float(g_prev2))
                    if isinstance(g_prev2, (int, float))
                    else None
                )
                if prev_dod is not None and abs(prev_dod) >= global_line and (prev_dod > 0) == (dod > 0):
                    pass
                else:
                    reasons.append(f"全球 TVL 日环比 {fmt_pct(dod)}（线 ±{global_line:g}%）")
            else:
                reasons.append(f"全球 TVL 日环比 {fmt_pct(dod)}（线 ±{global_line:g}%）")

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
    rows: list[dict[str, Any]],
    as_of: str,
    *,
    chain_qoq_line: float,
) -> tuple[bool, list[str], str | None]:
    """在 as_of 当天若刚完成某完整季/月，检查季度特殊事件。返回 (hit, reasons, event_id_suffix)。"""
    day = date.fromisoformat(as_of)
    reasons: list[str] = []
    suffix: str | None = None

    # 仅在「该日是某完整月最后一天」时评估月/季事件，避免每天重复
    if day.day != monthrange(day.year, day.month)[1]:
        return False, [], None

    months = complete_months(rows, "global")
    if len(months) >= 3:
        last3 = months[-3:]
        moms = [m[2] for m in last3]
        if all(isinstance(x, (int, float)) and x > 0 for x in moms):
            bits = "、".join(f"{lab} {fmt_pct(float(mom))}" for lab, _tvl, mom in last3)
            reasons.append(f"全球 TVL 连续三个完整月上涨（{bits}）")
            suffix = f"mom-streak3-{last3[-1][0]}"
        elif all(isinstance(x, (int, float)) and x < 0 for x in moms):
            bits = "、".join(f"{lab} {fmt_pct(float(mom))}" for lab, _tvl, mom in last3)
            reasons.append(f"全球 TVL 连续三个完整月下跌（{bits}）")
            suffix = f"mom-drop3-{last3[-1][0]}"

    # 完整季度末日（3/6/9/12 月）
    if day.month in (3, 6, 9, 12):
        quarters = complete_quarters(rows, "global")
        if len(quarters) >= 4:
            last = quarters[-1]
            prev3 = quarters[-4:-1]
            if (
                last[2] is not None
                and last[2] > 0
                and all(q[2] is not None and q[2] < 0 for q in prev3)
            ):
                reasons.append(
                    f"{last[0]} 全球 TVL 约 {fmt_yi(last[1])}，季环比 {fmt_pct(float(last[2]))}，"
                    f"此前连续三个季度下降后回升"
                )
                suffix = f"qoq-rebound-{last[0]}"
            elif last[2] is not None and abs(last[2]) >= 25:
                reasons.append(
                    f"{last[0]} 全球 TVL 约 {fmt_yi(last[1])}，季环比 {fmt_pct(float(last[2]))}（|Δ|≥25%）"
                )
                suffix = suffix or f"qoq-global-{last[0]}"

        for key in WATCH_KEYS:
            qs = complete_quarters(rows, key)
            if len(qs) < 2:
                continue
            lab, tvl, qoq = qs[-1]
            if qoq is None or qoq < chain_qoq_line:
                continue
            # 仅当本季末日触发
            q_year = int(lab[:4])
            q_n = int(lab[-1])
            if day.year == q_year and day.month == q_n * 3:
                reasons.append(
                    f"{LABELS.get(key, key)} {lab} TVL 约 {fmt_yi(tvl)}，季环比 {fmt_pct(qoq)}"
                    f"（线 {chain_qoq_line:g}%）"
                )
                suffix = suffix or f"qoq-chain-{key}-{lab}"

    return bool(reasons), reasons, suffix


def build_daily_text(rows: list[dict[str, Any]], day: str, latest_chains: list[dict[str, Any]]) -> str:
    curr = row_by_day(rows, day)
    assert curr is not None
    total = float(curr.get("global") or 0)
    y, m, d = day.split("-")
    top_bits: list[str] = []
    if latest_chains:
        for item in latest_chains[:5]:
            name = str(item.get("name") or "")
            tvl = float(item.get("tvl") or 0)
            top_bits.append(f"{name} 约 {fmt_yi(tvl)}")
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
    ton_s = ""
    ton = curr.get("ton")
    if isinstance(ton, (int, float)):
        ton_dod = ""
        if idx > 0:
            prev_ton = rows[idx - 1].get("ton")
            if isinstance(prev_ton, (int, float)) and prev_ton > 0:
                ton_dod = f"，较前一完整日 {fmt_pct(pct_change(float(ton), float(prev_ton)) or 0)}"
        ton_s = f"TON 约 {fmt_yi(float(ton))}{ton_dod}。"
    title = f"DeFi TVL：全球约 {fmt_yi(total)}"
    body = (
        f"据吴说数据中心统计，截至 {int(y)} 年 {int(m)} 月 {int(d)} 日，"
        f"全球 DeFi TVL 约 {fmt_yi(total)}{dod}。"
        f"{ton_s}"
        f"分链靠前约：{top_s}。"
        f"口径为 DefiLlama（不含流动性质押与双计），与 CryptoRank 等第三方数字可能不一致。"
        f"\n{DASHBOARD_URL}"
    )
    return f"{title}\n{body}"


def build_threshold_text(
    rows: list[dict[str, Any]], day: str, reasons: list[str], *, historical: bool
) -> str:
    curr = row_by_day(rows, day) or {}
    total = float(curr.get("global") or 0)
    y, m, d = day.split("-")
    title = f"DeFi TVL 异动：{int(m)} 月 {int(d)} 日触发阈值"
    body = (
        f"吴说获悉，DefiLlama DeFi TVL 在 {int(y)} 年 {int(m)} 月 {int(d)} 日出现超阈值变化。"
        f"触发原因：{'；'.join(reasons)}。"
        f"当日全球 TVL 约 {fmt_yi(total)}。"
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
    title = f"DeFi TVL 季度/连续异动：截至 {int(y)} 年 {int(m)} 月"
    body = (
        f"据吴说数据中心统计，截至 {int(y)} 年 {int(m)} 月 {int(d)} 日出现需单独提示的趋势变化。"
        f"{'；'.join(reasons)}。"
        f"当日全球 TVL 约 {fmt_yi(total)}。"
        f"本条为【季度特殊警报】，与日常阈值分开；口径不含流动性质押与双计，不宜与 CryptoRank 外电数字直接对表。"
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
    parser.add_argument("--no-daily", action="store_true", help="不发日常报告（只发阈值/特殊）")
    args = parser.parse_args()
    if getattr(args, "history_scan", False):
        args.history_scan = False

    env = merge_env()
    global_line = env_float(env, "DEFI_TVL_GLOBAL_DOD_PCT", DEFAULT_GLOBAL_DOD_PCT)
    chain_line = env_float(env, "DEFI_TVL_CHAIN_DOD_PCT", DEFAULT_CHAIN_DOD_PCT)
    chain_qoq = env_float(env, "DEFI_TVL_CHAIN_QOQ_PCT", DEFAULT_CHAIN_QOQ_PCT)

    try:
        json_path = resolve_json_path(args.json or None)
        rows, latest_chains = load_doc(json_path)
    except (OSError, RuntimeError, FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    complete = [
        row
        for row in rows
        if isinstance(row.get("global"), (int, float)) and float(row["global"]) > 0
    ]
    if not complete:
        print("[ERROR] 无含 global 的完整日", file=sys.stderr)
        return 1
    latest = str(complete[-1]["time"])
    if str(rows[-1]["time"]) != latest:
        print(f"[defi-tvl] skip incomplete tip={rows[-1]['time']} → use {latest}")
    print(
        f"[defi-tvl] json={json_path} latest={latest} rows={len(rows)} complete={len(complete)} "
        f"global_dod={global_line:g}% chain_dod={chain_line:g}% chain_qoq={chain_qoq:g}%"
    )
    rows = complete

    briefs: list[dict[str, Any]] = []

    hit_s, why_s, suf = evaluate_special(rows, latest, chain_qoq_line=chain_qoq)
    if hit_s and suf:
        briefs.append(
            {
                "id": f"defi-tvl-special-{suf}",
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
                "id": f"defi-tvl-threshold-{latest}",
                "kind": "threshold",
                "historical": False,
                "text": build_threshold_text(rows, latest, why_t, historical=False),
            }
        )

    if not args.no_daily:
        briefs.append(
            {
                "id": f"defi-tvl-daily-{latest}",
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
        for day in reversed(scan_days):
            if day == latest or hist_n >= limit:
                continue
            # special first
            ok_s, why, suf = evaluate_special(rows, day, chain_qoq_line=chain_qoq)
            if ok_s and suf:
                msg_id = f"defi-tvl-special-{suf}"
                if msg_id not in sent_set or args.force:
                    briefs.append(
                        {
                            "id": msg_id,
                            "kind": "special",
                            "historical": True,
                            "text": build_special_text(rows, day, why, historical=True),
                        }
                    )
                    hist_n += 1
                    if hist_n >= limit:
                        continue
            ok_t, why_t = evaluate_daily_threshold(
                rows, day, global_line=global_line, chain_line=chain_line
            )
            if not ok_t:
                continue
            msg_id = f"defi-tvl-threshold-{day}"
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
        print(f"[defi-tvl] state → {STATE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
