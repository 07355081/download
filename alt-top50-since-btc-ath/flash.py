# -*- coding: utf-8 -*-
"""自 BTC ATH 起，当前前 ~50 山寨相对 BTC → Lark。

- 日常：每个上海自然日一条【实时日常报告】
- 阈值：【历史阈值警报】——跑赢占比≥线且刚越过 / 中位相对倍数创 history 新高
- 特殊：跑赢个数创 history 新高时，阈值正文加「特殊警报」提示（history-scan 同样）
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
    HERE / "output" / "json" / "alt_top50_btc_ath_latest.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "alt_top50_btc_ath_latest.json",
    Path("/root/dashboard/public/json/alt_top50_btc_ath_latest.json"),
]
HISTORY_DIRS = [
    HERE / "output" / "json" / "alt_top50_btc_ath_history",
    ROOT.parent / "data-dashboard" / "public" / "json" / "alt_top50_btc_ath_history",
    Path("/root/dashboard/public/json/alt_top50_btc_ath_history"),
]
STATE_PATH = HERE / "output" / "alt_top50_btc_ath_lark.json"
LOG_PATH = HERE / "flash.log"

ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

TZ_CN = timezone(timedelta(hours=8))
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/alt-top50-btc-ath"
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
LARK_MARK_SPECIAL = "【特殊警报】"
SOURCE_NOTE = (
    "口径近似：抓取日市值前约 50（去 BTC/稳定币），自 BTC ATH 日至今相对倍数；"
    "不是 Glassnode ATH 当日冻结前 50。"
)
HISTORY_SCAN_DAYS = 60
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
        "ALT50_BEAT_SHARE_LINE",
        "ALT50_REL_MEDIAN_LINE",
        "ALT50_FLASH_PUSH",
    ):
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
    raise FileNotFoundError("找不到 alt_top50_btc_ath_latest.json，请先跑 download.py")


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
    leaders = doc.get("leaders") if isinstance(doc.get("leaders"), list) else []
    return {
        "day": day,
        "ath_day": str(doc.get("ath_day") or "")[:10],
        "window_days": int(doc.get("window_days") or 0),
        "n": int(doc.get("n") or 0),
        "n_priced": int(doc.get("n_priced") or 0),
        "n_beat_btc": int(doc.get("n_beat_btc") or 0),
        "beat_share": float(doc.get("beat_share") or 0),
        "btc_pct": float(doc.get("btc_pct") or 0),
        "median_rel": float(doc.get("median_rel") or 0),
        "avg_rel": float(doc.get("avg_rel") or 0),
        "leaders": leaders,
        "updated_at": str(doc.get("updated_at") or ""),
        "thresholds": doc.get("thresholds") if isinstance(doc.get("thresholds"), dict) else {},
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


def leaders_phrase(leaders: list[Any], *, limit: int = 5) -> str:
    bits: list[str] = []
    for row in leaders[:limit]:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "")
        try:
            rel = float(row.get("rel") or 0)
        except (TypeError, ValueError):
            continue
        if not sym:
            continue
        bits.append(f"{sym} {rel:.2f}x")
    return "、".join(bits) if bits else "无"


def evaluate_threshold(
    curr: dict[str, Any], history: list[dict[str, Any]], env: dict[str, str]
) -> tuple[bool, list[str], bool]:
    """返回 (是否阈值, 原因, 是否特殊——跑赢个数创历史新高)。"""
    reasons: list[str] = []
    special = False
    earlier = [h for h in history if h["day"] < curr["day"] and h["n_priced"] > 0]

    try:
        beat_line = float(env.get("ALT50_BEAT_SHARE_LINE") or curr["thresholds"].get("beat_share_line") or 0.5)
    except (TypeError, ValueError):
        beat_line = 0.5
    try:
        med_line = float(env.get("ALT50_REL_MEDIAN_LINE") or curr["thresholds"].get("median_rel_line") or 1.0)
    except (TypeError, ValueError):
        med_line = 1.0

    if earlier:
        prev = earlier[-1]
        # 刚越过跑赢占比线
        if curr["beat_share"] >= beat_line and prev["beat_share"] < beat_line:
            reasons.append(
                f"跑赢 BTC 占比 {curr['beat_share']*100:.1f}% 刚越过 {beat_line*100:.0f}% 线"
                f"（前值 {prev['beat_share']*100:.1f}%）"
            )
        max_med = max(h["median_rel"] for h in earlier)
        if curr["median_rel"] > max_med + 0.01 and curr["median_rel"] >= med_line:
            reasons.append(
                f"相对 BTC 倍数中位数 {curr['median_rel']:.2f}x 创近 {len(earlier)} 日新高"
                f"（此前最高 {max_med:.2f}x）"
            )
        max_beat = max(h["n_beat_btc"] for h in earlier)
        if curr["n_beat_btc"] > max_beat and curr["n_beat_btc"] >= 10:
            special = True
            reasons.append(
                f"跑赢 BTC 的币种数 {curr['n_beat_btc']} 创近 {len(earlier)} 日新高"
                f"（此前最高 {max_beat}）"
            )
    else:
        # 首日：仅在极端集中/扩散时提示，避免空 history 天天警报
        if curr["beat_share"] >= 0.6 and curr["n_beat_btc"] >= 20:
            reasons.append(
                f"首日快照跑赢占比已达 {curr['beat_share']*100:.1f}%（{curr['n_beat_btc']}/{curr['n_priced']}）"
            )

    return bool(reasons), reasons, special


def build_daily_text(snap: dict[str, Any]) -> str:
    day = snap["day"]
    y, m, d = day.split("-")
    ath = snap["ath_day"] or "—"
    title = (
        f"{int(m)} 月 {int(d)} 日山寨 vs BTC（自 ATH）："
        f"{snap['n_beat_btc']}/{snap['n_priced']} 跑赢"
    )
    body = (
        f"据吴说数据中心统计（CoinGecko 市值榜 + DefiLlama 日价，BTC ATH 日取自 Binance 日收盘最高），"
        f"截至 {y}-{m}-{d}，以 {ath} 为 BTC 历史新高日、窗口约 {snap['window_days']} 天："
        f"当前市值前约 {snap['n']} 只山寨（去稳定币）中有完整价的 {snap['n_priced']} 只，"
        f"其中 {snap['n_beat_btc']} 只相对 BTC 跑赢（相对倍数>1），占比 {snap['beat_share']*100:.1f}%；"
        f"同期 BTC 涨跌约 {snap['btc_pct']:+.1f}%，山寨相对倍数中位数约 {snap['median_rel']:.2f}x。"
        f"表现靠前：{leaders_phrase(snap['leaders'])}。"
        f"{SOURCE_NOTE}"
    )
    return f"{title}\n{body}\n{PAGE}"


def build_threshold_text(
    snap: dict[str, Any],
    reasons: list[str],
    *,
    historical: bool,
    special: bool,
) -> str:
    day = snap["day"]
    y, m, d = int(day[:4]), int(day[5:7]), int(day[8:10])
    title = f"山寨相对 BTC（自 ATH）异动：{y} 年 {m} 月 {d} 日触发阈值"
    if special:
        title = f"【特殊】{title}"
    body = (
        f"吴说获悉，自 BTC ATH（{snap['ath_day'] or '—'}）起算的大市值山寨相对表现在 "
        f"{y}-{m:02d}-{d:02d} 出现超阈值变化。"
        f"触发原因：{'；'.join(reasons)}。"
        f"当日样本 {snap['n_priced']} 只中 {snap['n_beat_btc']} 只跑赢，"
        f"占比 {snap['beat_share']*100:.1f}%，中位相对倍数 {snap['median_rel']:.2f}x，"
        f"BTC 区间约 {snap['btc_pct']:+.1f}%；靠前：{leaders_phrase(snap['leaders'])}。"
        f"本条为阈值异动提醒，不是例行日报。{SOURCE_NOTE}"
    )
    if historical:
        found = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
        body += f"本条为历史回扫发现（数据日 {day}，发现时间 {found} UTC+8），请勿当成今日刚发生。"
    return f"{title}\n{body}\n{PAGE}"


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
    if (env.get("ALT50_FLASH_PUSH") or "1").strip() == "0" and args.push:
        print("[alt-top50-since-btc-ath] ALT50_FLASH_PUSH=0，跳过推送")
        return 0

    try:
        latest_path = resolve_latest(args.json or None)
        curr = load_snap(latest_path)
        history = load_history()
    except (OSError, RuntimeError, FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    print(
        f"[alt-top50-since-btc-ath] json={latest_path} day={curr['day']} "
        f"ath={curr['ath_day']} beat={curr['n_beat_btc']}/{curr['n_priced']} hist={len(history)}"
    )

    briefs: list[dict[str, Any]] = []
    earlier = [h for h in history if h["day"] < curr["day"]]
    hit, reasons, special = evaluate_threshold(curr, earlier, env)
    if hit:
        briefs.append(
            {
                "id": f"alt50-btc-ath-threshold-{curr['day']}",
                "kind": "threshold",
                "historical": False,
                "special": special,
                "text": build_threshold_text(
                    curr, reasons, historical=False, special=special
                ),
            }
        )
    briefs.append(
        {
            "id": f"alt50-btc-ath-daily-{curr['day']}",
            "kind": "daily",
            "historical": False,
            "special": False,
            "text": build_daily_text(curr),
        }
    )

    state = load_state()
    sent_set = set(state.get("sentIds") or [])

    if args.history_scan:
        hist_n = 0
        limit = max(0, int(args.history_limit))
        for snap in reversed(history):
            day = snap["day"]
            if day == curr["day"]:
                continue
            if hist_n >= limit:
                break
            msg_id = f"alt50-btc-ath-threshold-{day}"
            if msg_id in sent_set and not args.force:
                continue
            prior = [h for h in history if h["day"] < day]
            ok, why, sp = evaluate_threshold(snap, prior, env)
            if not ok:
                continue
            briefs.append(
                {
                    "id": msg_id,
                    "kind": "threshold",
                    "historical": True,
                    "special": sp,
                    "text": build_threshold_text(snap, why, historical=True, special=sp),
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
        if brief["kind"] == "threshold" and brief.get("special") and brief.get("historical"):
            mark = f"{LARK_MARK_THRESHOLD}\n{LARK_MARK_SPECIAL}"
        elif brief["kind"] == "threshold" and brief.get("special"):
            mark = f"{LARK_MARK_THRESHOLD}\n{LARK_MARK_SPECIAL}"
        elif brief["kind"] == "threshold":
            mark = LARK_MARK_THRESHOLD
        else:
            mark = LARK_MARK_DAILY
        text = with_mark(mark, brief["text"])
        already = brief["id"] in state["sentIds"]
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
            print(f"skip {brief['id']}")
            continue
        if brief["kind"] == "threshold" and brief.get("special"):
            mark = f"{LARK_MARK_THRESHOLD}\n{LARK_MARK_SPECIAL}"
        elif brief["kind"] == "threshold":
            mark = LARK_MARK_THRESHOLD
        else:
            mark = LARK_MARK_DAILY
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
        print(f"[alt-top50-since-btc-ath] state → {STATE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
