# -*- coding: utf-8 -*-
"""Strategy 官网 BTC 持仓跳变 → Lark（仅阈值，无每日增持流水）。

口径：api.strategy.com 日快照（与 /dashboard/treasuries 顶部 KPI 同源）。
不含平均成本 / 浮盈亏 / STRC 分项回购；SEC 8-K 确认待 v2。

只发最新快照日，不回扫历史。
- 阈值：【历史阈值警报】——相对上一快照日 |Δholdings| ≥ 线（默认 500）
- 特殊：同条叠加【特殊警报】——当前持仓创已存快照新高
- 默认不发【实时日常报告】；正文为可发稿新闻体

用法：
  python flash.py
  python flash.py --push --no-daily
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    HERE / "output" / "json" / "latest.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "strategy-mstr" / "latest.json",
    Path("/root/dashboard/public/json/strategy-mstr/latest.json"),
]
SNAPSHOT_CANDIDATES = [
    HERE / "output" / "json" / "snapshots",
    Path("/root/data-download/strategy-mstr/output/json/snapshots"),
]
STATE_PATH = HERE / "output" / "strategy_mstr_lark.json"
LOG_PATH = HERE / "flash.log"

ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

TZ_CN = timezone(timedelta(hours=8))
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
LARK_MARK_SPECIAL = "【特殊警报】"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/treasuries"

DEFAULT_HOLDINGS_DELTA = 500.0
HISTORY_SCAN_DAYS = 30
DEFAULT_HISTORY_LIMIT = 4
BTC_SUPPLY_ASSUMED = 21_000_000.0


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
        "STRATEGY_MSTR_HOLDINGS_DELTA_BTC",
        "STRATEGY_MSTR_HOLDINGS_ATH",
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


def env_bool(env: dict[str, str], key: str, default: bool) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def resolve_latest(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 strategy-mstr latest.json，请先跑 1.download.py")


def resolve_snapshot_dir() -> Path | None:
    for path in SNAPSHOT_CANDIDATES:
        if path.is_dir():
            return path
    return None


def load_doc(path: Path) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    data = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} 无 data")
    return data


def shanghai_day_from_fetched(fetched: str) -> str:
    """fetched_at 多为 UTC ISO；告警日用上海日历，与 snapshots 文件名对齐。"""
    text = (fetched or "").strip()
    if len(text) >= 19 and text[4] == "-" and text[10] in ("T", " "):
        try:
            raw = text.replace("Z", "+00:00")
            if raw.endswith("+00:00") or "+" in raw[11:] or raw.count("-") >= 3:
                dt = datetime.fromisoformat(raw)
            else:
                dt = datetime.fromisoformat(text[:19]).replace(tzinfo=timezone.utc)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(TZ_CN).date().isoformat()
        except ValueError:
            pass
    return datetime.now(TZ_CN).date().isoformat()


def extract_kpis(data: dict[str, Any], *, day_override: str | None = None) -> dict[str, Any]:
    btc = data.get("btc") if isinstance(data.get("btc"), dict) else {}
    holdings = btc.get("holdings")
    pct = btc.get("pct_of_supply")
    reserve = btc.get("reserve_usd")
    mnav = btc.get("mnav")
    price = btc.get("price")
    fetched = str(data.get("fetched_at") or "")
    day = day_override or shanghai_day_from_fetched(fetched)
    if pct is None and isinstance(holdings, (int, float)):
        pct = float(holdings) / BTC_SUPPLY_ASSUMED * 100.0
    return {
        "day": day,
        "fetched_at": fetched,
        "holdings": float(holdings) if isinstance(holdings, (int, float)) else None,
        "pct_of_supply": float(pct) if isinstance(pct, (int, float)) else None,
        "reserve_usd": float(reserve) if isinstance(reserve, (int, float)) else None,
        "mnav": float(mnav) if isinstance(mnav, (int, float)) else None,
        "btc_price": float(price) if isinstance(price, (int, float)) else None,
    }


def load_snapshot_kpis(snap_dir: Path, day: str) -> dict[str, Any] | None:
    path = snap_dir / f"{day}.json"
    if not path.is_file():
        return None
    try:
        return extract_kpis(load_doc(path), day_override=day)
    except (OSError, RuntimeError, json.JSONDecodeError, ValueError):
        return None


def list_snapshot_days(snap_dir: Path) -> list[str]:
    return sorted(p.stem for p in snap_dir.glob("????-??-??.json"))


def previous_snapshot_day(snap_dir: Path, day: str) -> str | None:
    days = [d for d in list_snapshot_days(snap_dir) if d < day]
    return days[-1] if days else None


def resolve_curr_day(snap_dir: Path | None, latest_kpis: dict[str, Any]) -> str:
    """优先用最新快照文件名；否则用上海日。"""
    if snap_dir:
        days = list_snapshot_days(snap_dir)
        if days:
            return days[-1]
    return latest_kpis["day"]


def fmt_btc(n: float) -> str:
    if abs(n) >= 1000:
        return f"{n:,.0f}"
    return f"{n:,.3f}"


def fmt_pct(p: float, digits: int = 2) -> str:
    return f"{p:.{digits}f}%"


def evaluate_threshold(
    curr: dict[str, Any],
    prev: dict[str, Any] | None,
    history_holdings: list[float],
    *,
    delta_line: float,
    ath_enabled: bool,
) -> tuple[bool, list[str], bool]:
    """返回 (是否阈值, 原因, 是否特殊=跳变过线且持仓新高)。

    仅 |Δholdings|≥线才告警；新高不单独推，只在跳变过线时叠加【特殊警报】。
    """
    reasons: list[str] = []
    special = False
    h = curr.get("holdings")
    if not isinstance(h, (int, float)):
        return False, [], False

    jumped = False
    if prev and isinstance(prev.get("holdings"), (int, float)):
        delta = float(h) - float(prev["holdings"])
        if abs(delta) + 1e-9 >= delta_line:
            jumped = True
            direction = "增加" if delta > 0 else "减少"
            reasons.append(
                f"相对 {prev['day']} 持仓{direction} {fmt_btc(abs(delta))} 枚"
                f"（{fmt_btc(float(prev['holdings']))} → {fmt_btc(float(h))}，线 {fmt_btc(delta_line)}）"
            )

    if not jumped:
        return False, [], False

    earlier = [x for x in history_holdings if isinstance(x, (int, float))]
    if ath_enabled and earlier and float(h) > max(earlier) + 1e-9:
        special = True
        reasons.append(f"BTC 持仓 {fmt_btc(float(h))} 枚创已存快照新高")

    return True, reasons, special


def build_daily_text(curr: dict[str, Any]) -> str:
    """仅 --daily 调试用，默认不推。"""
    h = curr.get("holdings")
    pct = curr.get("pct_of_supply")
    mnav = curr.get("mnav")
    day = curr["day"]
    y, m, d = day.split("-")
    hold_s = fmt_btc(float(h)) if isinstance(h, (int, float)) else "—"
    pct_s = fmt_pct(float(pct)) if isinstance(pct, (int, float)) else "—"
    mnav_s = f"{float(mnav):.4f}" if isinstance(mnav, (int, float)) else "—"
    title = f"Strategy 官网：持有约 {hold_s} 枚 BTC（约占供给 {pct_s}）"
    body = (
        f"据吴说数据中心统计（Strategy 官网口径），截至 {int(y)} 年 {int(m)} 月 {int(d)} 日快照，"
        f"公司持有约 {hold_s} 枚 BTC，约占比特币总供应量的 {pct_s}；官网 mNAV 约 {mnav_s}。"
        f"本条为调试例行摘要，正式管道默认不推日常流水。"
        f"\n{DASHBOARD_URL}"
    )
    return f"{title}\n{body}"


def build_threshold_text(
    curr: dict[str, Any],
    reasons: list[str],
    *,
    historical: bool,
    special: bool,
) -> str:
    del historical, special  # 分类靠首行标记；正文只留新闻
    h = curr.get("holdings")
    pct = curr.get("pct_of_supply")
    reserve = curr.get("reserve_usd")
    day = curr["day"]
    y, m, d = day.split("-")
    hold_s = fmt_btc(float(h)) if isinstance(h, (int, float)) else "—"
    pct_s = fmt_pct(float(pct)) if isinstance(pct, (int, float)) else "—"
    bits = [
        f"截至 {int(y)} 年 {int(m)} 月 {int(d)} 日，Strategy 持有约 {hold_s} 枚 BTC，约占比特币供应量 {pct_s}",
    ]
    if isinstance(reserve, (int, float)) and float(reserve) > 0:
        bits.append(f"官网 BTC 储备价值约 {float(reserve) / 1e8:.2f} 亿美元")
    if reasons:
        bits.append("；".join(reasons))
    body = "。".join(bits) + "。"
    return (
        f"据吴说数据中心统计，{body}"
        "口径为 Strategy 官网 api.strategy.com 日快照；不含平均购入价与总购入成本。"
        f"\n{DASHBOARD_URL}"
    )


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
    body = as_news(text)
    heads: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s in (LARK_MARK_THRESHOLD, LARK_MARK_SPECIAL, LARK_MARK_DAILY):
            heads.append(s)
        elif not s:
            continue
        else:
            break
    if heads:
        body = "\n".join(heads) + "\n" + body
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": body}}
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


def history_holdings_before(snap_dir: Path | None, day: str) -> list[float]:
    if not snap_dir:
        return []
    out: list[float] = []
    for name in list_snapshot_days(snap_dir):
        if name >= day:
            continue
        kpis = load_snapshot_kpis(snap_dir, name)
        if kpis and isinstance(kpis.get("holdings"), (int, float)):
            out.append(float(kpis["holdings"]))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--history-scan", action="store_true")
    parser.add_argument("--history-limit", type=int, default=DEFAULT_HISTORY_LIMIT)
    parser.add_argument("--json", default="")
    parser.add_argument(
        "--daily",
        action="store_true",
        help="可选：额外发【实时日常报告】（正式管道不要开）",
    )
    parser.add_argument(
        "--no-daily",
        action="store_true",
        help="兼容旧参数；默认本就不发日常",
    )
    args = parser.parse_args()
    if getattr(args, "history_scan", False):
        args.history_scan = False

    env = merge_env()
    delta_line = env_float(env, "STRATEGY_MSTR_HOLDINGS_DELTA_BTC", DEFAULT_HOLDINGS_DELTA)
    ath_enabled = env_bool(env, "STRATEGY_MSTR_HOLDINGS_ATH", True)

    try:
        latest_path = resolve_latest(args.json or None)
        latest_kpis = extract_kpis(load_doc(latest_path))
    except (OSError, RuntimeError, FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    snap_dir = resolve_snapshot_dir()
    curr_day = resolve_curr_day(snap_dir, latest_kpis)
    curr = load_snapshot_kpis(snap_dir, curr_day) if snap_dir else None
    if not curr:
        curr = dict(latest_kpis)
        curr["day"] = curr_day

    prev_day = previous_snapshot_day(snap_dir, curr_day) if snap_dir else None
    prev = load_snapshot_kpis(snap_dir, prev_day) if snap_dir and prev_day else None
    hist_h = history_holdings_before(snap_dir, curr_day)

    print(
        f"[strategy-mstr] json={latest_path} day={curr['day']} "
        f"holdings={curr.get('holdings')} delta_line={delta_line:g} "
        f"prev={prev['day'] if prev else None} snaps={snap_dir}"
    )

    briefs: list[dict[str, Any]] = []
    hit, reasons, special = evaluate_threshold(
        curr, prev, hist_h, delta_line=delta_line, ath_enabled=ath_enabled
    )
    if hit:
        briefs.append(
            {
                "id": f"strategy-mstr-threshold-{curr['day']}",
                "kind": "threshold",
                "historical": False,
                "special": special,
                "text": build_threshold_text(
                    curr, reasons, historical=False, special=special
                ),
            }
        )

    if args.daily and not args.no_daily:
        briefs.append(
            {
                "id": f"strategy-mstr-daily-{curr['day']}",
                "kind": "daily",
                "historical": False,
                "special": False,
                "text": build_daily_text(curr),
            }
        )

    state = load_state()
    sent_set = set(state.get("sentIds") or [])

    if args.history_scan and snap_dir:
        days = list_snapshot_days(snap_dir)[-HISTORY_SCAN_DAYS:]
        hist_n = 0
        limit = max(0, int(args.history_limit))
        for day in reversed(days):
            if day == curr["day"] or hist_n >= limit:
                continue
            msg_id = f"strategy-mstr-threshold-{day}"
            if msg_id in sent_set and not args.force:
                continue
            row = load_snapshot_kpis(snap_dir, day)
            if not row:
                continue
            row_prev_day = previous_snapshot_day(snap_dir, day)
            row_prev = load_snapshot_kpis(snap_dir, row_prev_day) if row_prev_day else None
            row_hist = history_holdings_before(snap_dir, day)
            ok, why, sp = evaluate_threshold(
                row, row_prev, row_hist, delta_line=delta_line, ath_enabled=ath_enabled
            )
            if not ok:
                continue
            briefs.append(
                {
                    "id": msg_id,
                    "kind": "threshold",
                    "historical": True,
                    "special": sp,
                    "text": build_threshold_text(row, why, historical=True, special=sp),
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

    if not unique:
        print("[strategy-mstr] 无阈值命中，不推送（正式管道不做日常流水）")

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

    if not unique:
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
        text = with_mark(mark_for(brief), brief["text"])
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
