# -*- coding: utf-8 -*-
"""Anthropic 上市前多所 OI：周频日常 + 近 60 日倍率历史阈值 → Lark。

数据：tradfi/output/json/tradfi-oi/*ANTHROPIC*1d.json（VPS 日更，不读 Kaiko、不占 Dune）。

日常：每个上海自然周最多一条，写最新全市场 OI、区间倍率与 Binance 份额；普通新闻，不加【实时日常报告】。
历史阈值：近 LOOKBACK 日全市场 OI 倍率 ≥ MULT_MIN 且期末绝对值 ≥ ABS_MIN 时发【历史阈值警报】，
写明不是当日例行连推；同一起止日窗口只发一次。发送失败不记已发。

用法：
  python tradfi/anthropic_oi_flash.py
  python tradfi/anthropic_oi_flash.py --push
  python tradfi/anthropic_oi_flash.py --push --force-weekly
  python tradfi/anthropic_oi_flash.py --push --force-hist
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_DIR = HERE / "output" / "json" / "tradfi-oi"
STATE_PATH = HERE / "output" / "anthropic_oi_flash_state.json"
LOG_PATH = HERE / "anthropic_oi_flash.log"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/tradfi"
MARK_HIST = "【历史阈值警报】"
TZ_CN = timezone(timedelta(hours=8))

LOOKBACK_DAYS = int(os.environ.get("ANTHROPIC_OI_LOOKBACK_DAYS") or 60)
MULT_MIN = float(os.environ.get("ANTHROPIC_OI_MULT_MIN") or 3.0)
ABS_MIN_USD = float(os.environ.get("ANTHROPIC_OI_ABS_MIN_USD") or 30_000_000.0)

ENV_FILES = [
    ROOT / ".env",
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
]


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


def load_env() -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in ENV_FILES:
        merged.update(load_env_file(path))
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            merged[key] = os.environ[key]
    return merged


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"weekly_sent": {}, "hist_sent": {}, "updated_at": ""}
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"weekly_sent": {}, "hist_sent": {}, "updated_at": ""}
    if not isinstance(raw, dict):
        return {"weekly_sent": {}, "hist_sent": {}, "updated_at": ""}
    raw.setdefault("weekly_sent", {})
    raw.setdefault("hist_sent", {})
    return raw


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def fmt_usd(value: float) -> str:
    if value >= 1e8:
        return f"{value / 1e8:.2f} 亿美元"
    if value >= 1e4:
        return f"{value / 1e4:,.0f} 万美元"
    return f"{value:,.0f} 美元"


def fmt_mult(mult: float) -> str:
    return f"{mult:.1f}".rstrip("0").rstrip(".") + " 倍"


def ms_to_date(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).date()


def load_exchange_series(json_dir: Path) -> dict[str, dict[date, float]]:
    """exchange -> {date: oi_usd}，同日取 time 最大的一条。"""
    if not json_dir.is_dir():
        raise FileNotFoundError(f"缺少 OI JSON 目录: {json_dir}")
    paths = sorted(json_dir.glob("*ANTHROPIC*1d.json"))
    if not paths:
        raise FileNotFoundError(f"{json_dir} 下无 *ANTHROPIC*1d.json")

    out: dict[str, dict[date, float]] = {}
    for path in paths:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[warn] 跳过 {path.name}: {exc}", file=sys.stderr)
            continue
        if not isinstance(doc, dict):
            continue
        exchange = str(doc.get("exchange") or path.stem.split("_", 1)[0] or "unknown")
        rows = doc.get("data")
        if not isinstance(rows, list):
            continue
        series: dict[date, tuple[int, float]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                t = int(row["time"])
                oi = float(row["oi_usd"])
            except (KeyError, TypeError, ValueError):
                continue
            d = ms_to_date(t)
            prev = series.get(d)
            if prev is None or t >= prev[0]:
                series[d] = (t, oi)
        if series:
            out[exchange] = {d: oi for d, (_t, oi) in series.items()}
    if not out:
        raise RuntimeError("未能解析任何 ANTHROPIC OI 序列")
    return out


def aggregate_daily(by_ex: dict[str, dict[date, float]]) -> dict[date, dict[str, float]]:
    """date -> {exchange: oi, '_total': sum}。"""
    all_dates: set[date] = set()
    for series in by_ex.values():
        all_dates.update(series)
    daily: dict[date, dict[str, float]] = {}
    for d in sorted(all_dates):
        row: dict[str, float] = {}
        total = 0.0
        for ex, series in by_ex.items():
            if d not in series:
                continue
            oi = float(series[d])
            row[ex] = oi
            total += oi
        row["_total"] = total
        daily[d] = row
    return daily


def pick_window(
    daily: dict[date, dict[str, float]], lookback: int
) -> tuple[date, date, float, float] | None:
    """取最新有数据日为 end，start = end - lookback；两端需有全市场合计。"""
    if not daily:
        return None
    end = max(daily)
    start = end - timedelta(days=lookback)
    # 若起日无点，向后最多找 3 天；终日无点则失败（end 已是有点日）
    start_use = start
    for i in range(4):
        cand = start + timedelta(days=i)
        if cand in daily and daily[cand]["_total"] > 0:
            start_use = cand
            break
    else:
        # 向前找最近有点日（不晚于 start）
        earlier = [d for d in daily if d <= start and daily[d]["_total"] > 0]
        if not earlier:
            return None
        start_use = max(earlier)
    oi0 = float(daily[start_use]["_total"])
    oi1 = float(daily[end]["_total"])
    if oi0 <= 0 or oi1 <= 0:
        return None
    return start_use, end, oi0, oi1


def binance_share(row: dict[str, float]) -> float | None:
    total = float(row.get("_total") or 0)
    bn = float(row.get("Binance") or 0)
    if total <= 0:
        return None
    return bn / total * 100.0


def exchange_list(daily: dict[date, dict[str, float]], d: date) -> list[str]:
    row = daily.get(d) or {}
    names = [k for k, v in row.items() if k != "_total" and float(v) > 0]
    # 稳定顺序：金额降序
    names.sort(key=lambda n: float(row[n]), reverse=True)
    return names


def iso_week_id(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def build_weekly_text(
    start: date,
    end: date,
    oi0: float,
    oi1: float,
    mult: float,
    share: float | None,
    venues: list[str],
) -> str:
    share_s = f"{share:.0f}%" if share is not None else "—"
    venue_s = "、".join(venues) if venues else "—"
    return (
        f"据吴说数据中心统计，Anthropic 上市前相关永续未平仓合计由 "
        f"{start.month} 月 {start.day} 日约 {fmt_usd(oi0)} 增至 "
        f"{end.month} 月 {end.day} 日约 {fmt_usd(oi1)}，约合 {fmt_mult(mult)}；"
        f"在统计交易所（{venue_s}）中，Binance 约占 {share_s}。"
        f"Anthropic 尚未公开上市、无公开股价，口径为自有多所 TradFi OI 加总，非 Kaiko。"
        f"\n{DASHBOARD_URL}"
    )


def build_hist_text(
    start: date,
    end: date,
    oi0: float,
    oi1: float,
    mult: float,
    share: float | None,
    venues: list[str],
) -> str:
    share_s = f"{share:.0f}%" if share is not None else "—"
    venue_s = "、".join(venues) if venues else "—"
    return "\n".join(
        [
            MARK_HIST,
            f"近 {LOOKBACK_DAYS} 日 Anthropic 上市前多所未平仓倍率已达历史阈值（≥{MULT_MIN:g}×），不是当日例行连推。",
            "",
            (
                f"据吴说数据中心统计，Anthropic 上市前相关永续未平仓合计由 "
                f"{start.month} 月 {start.day} 日约 {fmt_usd(oi0)} 增至 "
                f"{end.month} 月 {end.day} 日约 {fmt_usd(oi1)}，约合 {fmt_mult(mult)}；"
                f"在统计交易所（{venue_s}）中，Binance 约占 {share_s}。"
                f"数字用自有 TradFi OI 加总，不抄 Kaiko。"
            ),
            DASHBOARD_URL,
        ]
    )


def send_lark(webhook: str, secret: str | None, text: str) -> dict[str, Any]:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(datetime.now().timestamp()))
        digest = hmac.new(f"{ts}\n{secret}".encode(), digestmod=hashlib.sha256).digest()
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(digest).decode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body) if body else {}


def append_log(stamp: str, kind: str, msg_id: str, ok: bool, text: str, extra: str = "") -> None:
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(
            f"=== {stamp} kind={kind} id={msg_id} ok={ok} {extra}\n{text}\n\n"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force-weekly", action="store_true", help="忽略本周已发去重")
    parser.add_argument("--force-hist", action="store_true", help="忽略历史窗口已发去重")
    parser.add_argument("--json-dir", default=str(JSON_DIR))
    parser.add_argument(
        "--weekly-weekday",
        type=int,
        default=int(os.environ.get("ANTHROPIC_OI_WEEKLY_WEEKDAY") or 0),
        help="上海时区 weekday：0=周一 … 6=周日；仅该日尝试周频（默认周一）",
    )
    args = parser.parse_args()

    try:
        by_ex = load_exchange_series(Path(args.json_dir))
        daily = aggregate_daily(by_ex)
        window = pick_window(daily, LOOKBACK_DAYS)
    except (OSError, RuntimeError, FileNotFoundError, ValueError) as exc:
        print(f"[skip] 数据不可用: {exc}", file=sys.stderr)
        return 1

    if window is None:
        print("[skip] 无法构成 lookback 窗口", file=sys.stderr)
        return 1

    start, end, oi0, oi1 = window
    mult = oi1 / oi0
    share = binance_share(daily[end])
    venues = exchange_list(daily, end)
    print(
        f"window {start} -> {end} oi0={oi0:.2f} oi1={oi1:.2f} mult={mult:.3f} "
        f"bn_share={share} venues={venues}"
    )

    now = datetime.now(TZ_CN)
    week_id = iso_week_id(now.date())
    hist_id = f"hist-{start.isoformat()}_{end.isoformat()}"
    weekly_id = f"weekly-{week_id}-{end.isoformat()}"

    hist_hit = mult >= MULT_MIN and oi1 >= ABS_MIN_USD
    print(
        f"hist_hit={hist_hit} (mult>={MULT_MIN:g} and oi1>={ABS_MIN_USD:g}) "
        f"week={week_id} weekday={now.weekday()}"
    )

    state = load_state()
    messages: list[tuple[str, str, str]] = []  # kind, id, text

    if hist_hit:
        text = build_hist_text(start, end, oi0, oi1, mult, share, venues)
        already = hist_id in (state.get("hist_sent") or {})
        if args.force_hist or not already:
            messages.append(("hist", hist_id, text))
            print("\n--- hist ---\n" + text)
        else:
            print(f"[skip] 历史窗口已发: {hist_id}", file=sys.stderr)

    # 周频：周一（可配）才发；历史已发同日仍可发周频（内容不同）
    weekly_due = now.weekday() == args.weekly_weekday
    if weekly_due or args.force_weekly:
        text = build_weekly_text(start, end, oi0, oi1, mult, share, venues)
        already = week_id in (state.get("weekly_sent") or {})
        if args.force_weekly or not already:
            messages.append(("weekly", weekly_id, text))
            print("\n--- weekly ---\n" + text)
        else:
            print(f"[skip] 本周已发周频: {week_id}", file=sys.stderr)
    else:
        print(f"[skip] 非周频日 weekday={now.weekday()} need={args.weekly_weekday}", file=sys.stderr)

    if not args.push:
        print("\n[dry-run]", file=sys.stderr)
        return 0

    if not messages:
        print("[no-push]", file=sys.stderr)
        return 0

    conf = load_env()
    webhook = (conf.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (conf.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 1

    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    sent_any = False
    for kind, msg_id, text in messages:
        try:
            result = send_lark(webhook, secret, text)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"[ERROR] Lark 失败 {msg_id}: {exc}", file=sys.stderr)
            append_log(stamp, kind, msg_id, False, text, extra=f"err={exc}")
            continue
        ok = str(result.get("code", "0")) == "0"
        append_log(stamp, kind, msg_id, ok, text, extra=f"resp={result}")
        if not ok:
            print(f"[ERROR] 推送失败 {msg_id}: {result}", file=sys.stderr)
            continue
        sent_any = True
        if kind == "hist":
            state.setdefault("hist_sent", {})[hist_id] = msg_id
            # 控制体积
            hist_sent = state["hist_sent"]
            if len(hist_sent) > 40:
                for k in list(hist_sent)[:-40]:
                    hist_sent.pop(k, None)
        else:
            state.setdefault("weekly_sent", {})[week_id] = msg_id
            weekly_sent = state["weekly_sent"]
            if len(weekly_sent) > 26:
                for k in list(weekly_sent)[:-26]:
                    weekly_sent.pop(k, None)
        print(f"[pushed] {msg_id}", file=sys.stderr)

    save_state(state)
    return 0 if sent_any else 1


if __name__ == "__main__":
    raise SystemExit(main())
