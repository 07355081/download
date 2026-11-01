"""美股盘中：日常简报 + 超阈值【历史阈值警报】→ 飞书。

阈值默认：任一只 |涨跌幅| ≥ 5%（可用 US_SESSION_THRESHOLD_PCT 覆盖）。
日常与阈值分两条；阈值用【历史阈值警报】抬头，日常用【实时日常报告】。
同一 id 只发一次；发送失败不记已发。

用法：
  python us_session_flash.py
  python us_session_flash.py --push
  python us_session_flash.py --push --history-only
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
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUTPUT = HERE / "output" / "json"
MOVES_PATH = OUTPUT / "tradfi-session-moves.json"
HISTORY_DIR = OUTPUT / "tradfi-session-moves-history"
# Prefer dashboard public/json copies when present (post copy_to_dashboard).
DASH_JSON = Path("/root/dashboard/public/json")
STATE_CANDIDATES = [
    Path("/root/dashboard/data/us-session-lark.json"),
    ROOT.parent / "data-dashboard" / "data" / "us-session-lark.json",
    HERE / "output" / "us-session-lark.json",
]
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

NY = ZoneInfo("America/New_York")
TZ_CN = timezone(timedelta(hours=8))
DEFAULT_THRESHOLD_PCT = 5.0
SEND_START_MIN = 11 * 60
SEND_END_MIN = 16 * 60
LARK_MARK_THRESHOLD = "【历史阈值警报】"
LARK_MARK_DAILY = "【实时日常报告】"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/tradfi"
# 与 session_moves.SESSION_BASKET 长度对齐（含 ORCL）
BASKET_N = 15
HTTP_TIMEOUT = 45


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

def load_env(path: Path) -> dict[str, str]:
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
        env.update(load_env(path))
    for k in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET", "US_SESSION_THRESHOLD_PCT"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def et_clock(now: datetime | None = None) -> tuple[str, int]:
    now = now or datetime.now(tz=NY)
    local = now.astimezone(NY)
    return local.date().isoformat(), local.hour * 60 + local.minute


def in_send_window(minutes: int) -> bool:
    return SEND_START_MIN <= minutes < SEND_END_MIN


def resolve_moves_path() -> Path:
    dash = DASH_JSON / "tradfi-session-moves.json"
    if dash.is_file():
        return dash
    return MOVES_PATH


def resolve_history_dir() -> Path:
    dash = DASH_JSON / "tradfi-session-moves-history"
    if dash.is_dir():
        return dash
    return HISTORY_DIR


def resolve_state_path() -> Path:
    for path in STATE_CANDIDATES:
        if path.parent.is_dir():
            return path
    return STATE_CANDIDATES[-1]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


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
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        body = resp.read().decode("utf-8", "replace")
    if not body:
        return
    parsed = json.loads(body)
    code = parsed.get("code", parsed.get("StatusCode"))
    if isinstance(code, int) and code != 0:
        raise RuntimeError(f"Lark rejected: {body[:200]}")


def with_mark(mark: str, text: str) -> str:
    body = text.lstrip("\ufeff")
    if body.startswith(mark):
        return body
    return f"{mark}\n{body}"


def zh_date(iso: str) -> str:
    _, m, d = iso.split("-")
    return f"{int(m)} 月 {int(d)} 日"


def majority(changes: list[float]) -> str:
    up = sum(1 for c in changes if c > 0)
    down = sum(1 for c in changes if c < 0)
    if down > up:
        return "下跌"
    if up > down:
        return "上涨"
    return "涨跌互现"


def move_text(row: dict[str, Any]) -> str:
    pct = float(row["change_pct"])
    verb = "跌" if pct < 0 else "涨"
    return f"{row['name_zh']}（{row['ticker']}）{verb} {abs(pct):.2f}%"


def floor_tenth(change_pct: float) -> str:
    rounded = round(change_pct * 10000) / 10000
    return f"{(int(rounded * 10 + 1e-8) / 10):.1f}"


def parse_rows(doc: dict[str, Any]) -> list[dict[str, Any]] | None:
    rows = doc.get("rows")
    if not isinstance(rows, list) or len(rows) != BASKET_N:
        return None
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        ticker = str(row.get("ticker") or "").upper()
        name = str(row.get("name_zh") or "").strip()
        group = row.get("group")
        pct = row.get("change_pct")
        asof = row.get("asof_ms")
        if not ticker or not name or group not in ("ai", "crypto"):
            return None
        if not isinstance(pct, (int, float)) or not isinstance(asof, (int, float)):
            return None
        out.append(
            {
                "ticker": ticker,
                "name_zh": name,
                "group": group,
                "change_pct": float(pct),
                "asof_ms": int(asof),
            }
        )
    return out


def quote_in_window(asof_ms: int, session_date: str) -> bool:
    local = datetime.fromtimestamp(asof_ms / 1000, tz=NY)
    mins = local.hour * 60 + local.minute
    return local.date().isoformat() == session_date and in_send_window(mins)


def format_daily(rows: list[dict[str, Any]], session_date: str) -> str:
    ai = [r for r in rows if r["group"] == "ai"]
    crypto = [r for r in rows if r["group"] == "crypto"]
    ai_word = majority([r["change_pct"] for r in ai])
    crypto_word = majority([r["change_pct"] for r in crypto])
    ai_phrase = "AI 相关股涨跌互现" if ai_word == "涨跌互现" else f"AI 相关股多数{ai_word}"
    crypto_phrase = (
        "加密相关股票涨跌互现" if crypto_word == "涨跌互现" else f"加密相关股票多数{crypto_word}"
    )
    body = (
        f"据吴说数据中心统计，美股盘中，{ai_phrase}，{'，'.join(move_text(r) for r in ai)}；"
        f"{crypto_phrase}，{'，'.join(move_text(r) for r in crypto)}。"
    )
    if not crypto:
        return body
    leader = max(crypto, key=lambda r: r["change_pct"])
    if leader["change_pct"] <= 0:
        return body
    lead = (
        f"{zh_date(session_date)}美股盘中，{leader['name_zh']} 领涨加密概念股，"
        f"涨幅超 {floor_tenth(leader['change_pct'])}%。"
    )
    return f"{lead}\n{body}"


def format_threshold(
    hits: list[dict[str, Any]],
    session_date: str,
    *,
    threshold_pct: float,
    historical: bool,
) -> str:
    top = hits[0]
    verb = "跌" if top["change_pct"] < 0 else "涨"
    title = (
        f"{zh_date(session_date)}美股盘中异动：{top['name_zh']}（{top['ticker']}）"
        f"{verb}超 {threshold_pct:g}%"
    )
    body = (
        f"据吴说数据中心统计，美股盘中，固定观察名单中有 {len(hits)} 只相对前收盘涨跌幅绝对值"
        f"达到或超过 {threshold_pct:g}%：{'，'.join(move_text(r) for r in hits)}。"
        "本条为阈值异动提醒，不是盘中例行简报。"
    )
    if historical:
        body += f"本条为历史回扫发现（交易日 {session_date}），请勿当成今日刚发生。"
    return f"{title}\n{body}"


def build_briefs(
    doc: dict[str, Any],
    *,
    today: str,
    minutes: int,
    threshold_pct: float,
    allow_historical: bool,
) -> list[dict[str, Any]]:
    if doc.get("complete") is not True:
        return []
    session = doc.get("session_date")
    if not isinstance(session, str):
        return []
    rows = parse_rows(doc)
    if not rows:
        return []
    hits = sorted(
        [r for r in rows if abs(r["change_pct"]) >= threshold_pct],
        key=lambda r: abs(r["change_pct"]),
        reverse=True,
    )
    out: list[dict[str, Any]] = []
    is_today = session == today
    if hits:
        if is_today and in_send_window(minutes) and all(
            quote_in_window(r["asof_ms"], session) for r in rows
        ):
            out.append(
                {
                    "id": f"us-session-threshold-{session}",
                    "kind": "threshold",
                    "historical": False,
                    "text": format_threshold(
                        hits, session, threshold_pct=threshold_pct, historical=False
                    ),
                }
            )
        elif (not is_today) and allow_historical:
            out.append(
                {
                    "id": f"us-session-threshold-{session}",
                    "kind": "threshold",
                    "historical": True,
                    "text": format_threshold(
                        hits, session, threshold_pct=threshold_pct, historical=True
                    ),
                }
            )
    if (
        is_today
        and in_send_window(minutes)
        and all(quote_in_window(r["asof_ms"], session) for r in rows)
    ):
        out.append(
            {
                "id": f"us-session-{session}",
                "kind": "daily",
                "historical": False,
                "text": format_daily(rows, session),
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--history-only", action="store_true", help="只扫历史归档，不发当日日常")
    parser.add_argument("--force", action="store_true", help="忽略已发送去重")
    args = parser.parse_args()

    env = merge_env()
    raw_th = (env.get("US_SESSION_THRESHOLD_PCT") or "").strip()
    try:
        threshold_pct = float(raw_th) if raw_th else DEFAULT_THRESHOLD_PCT
    except ValueError:
        threshold_pct = DEFAULT_THRESHOLD_PCT

    today, minutes = et_clock()
    briefs: list[dict[str, Any]] = []

    if not args.history_only:
        moves_path = resolve_moves_path()
        if moves_path.is_file():
            try:
                doc = load_json(moves_path)
                if isinstance(doc, dict):
                    briefs.extend(
                        build_briefs(
                            doc,
                            today=today,
                            minutes=minutes,
                            threshold_pct=threshold_pct,
                            allow_historical=False,
                        )
                    )
            except (OSError, json.JSONDecodeError) as exc:
                print(f"[WARN] live moves: {exc}", file=sys.stderr)

    hist_dir = resolve_history_dir()
    if hist_dir.is_dir():
        for path in sorted(hist_dir.glob("????-??-??.json"), reverse=True)[:40]:
            try:
                doc = load_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(doc, dict):
                continue
            briefs.extend(
                build_briefs(
                    doc,
                    today=today,
                    minutes=minutes,
                    threshold_pct=threshold_pct,
                    allow_historical=True,
                )
            )

    # de-dupe by id, prefer first (live/threshold before older)
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for brief in briefs:
        if brief["id"] in seen:
            continue
        seen.add(brief["id"])
        unique.append(brief)
    # threshold first, then daily; within kind newer dates first already
    unique.sort(key=lambda b: (0 if b["kind"] == "threshold" else 1, b["id"]), reverse=False)

    state_path = resolve_state_path()
    state = {"sentIds": [], "updatedAt": ""}
    if state_path.is_file():
        try:
            loaded = load_json(state_path)
            if isinstance(loaded, dict) and isinstance(loaded.get("sentIds"), list):
                state["sentIds"] = [x for x in loaded["sentIds"] if isinstance(x, str)]
        except (OSError, json.JSONDecodeError):
            pass

    print(
        f"[us-session] today_et={today} minutes={minutes} threshold={threshold_pct:g}% "
        f"briefs={len(unique)} state={state_path}"
    )
    if not unique:
        print("[us-session] nothing to send (waiting / incomplete / outside window)")
        return 0

    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    sent_any = False

    for brief in unique:
        mark = LARK_MARK_THRESHOLD if brief["kind"] == "threshold" else LARK_MARK_DAILY
        text = with_mark(mark, f"{brief['text']}\n\n{DASHBOARD_URL}")
        already = brief["id"] in state["sentIds"]
        print("---")
        print(f"id={brief['id']} kind={brief['kind']} historical={brief['historical']} already={already}")
        print(text)
        if not args.push:
            continue
        if already and not args.force:
            print("  → skip already-sent")
            continue
        if not webhook:
            print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
            return 2
        try:
            send_lark_text(webhook, secret, text)
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            print(f"[ERROR] Lark 失败: {exc}", file=sys.stderr)
            return 1
        print("  → Lark OK")
        if brief["id"] not in state["sentIds"]:
            state["sentIds"].append(brief["id"])
        sent_any = True
        time.sleep(0.35)

    if args.push and sent_any:
        state["sentIds"] = state["sentIds"][-120:]
        state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        save_json(state_path, state)
        print(f"[us-session] state saved → {state_path}")
    elif not args.push:
        print("\n[dry-run] 未推送")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
