# -*- coding: utf-8 -*-
"""TradeXYZ 日成交 → Lark。用 Hyperliquid 官方日 K，不新开 Dune。

- 日常：最近一个已结束的 UTC 日【实时日常报告】
- 阈值：|较前一日| ≥ 40% → 【历史阈值警报】+【特殊警报】
- --history-scan：回扫近 40 个已结束日里过线且未发过的，标明历史回扫

成交额 = 日 K 成交量 × 收盘价。HIP-3 份额是当天各 builder 市场加总。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE_PATH = HERE / "output" / "tradexyz_daily.json"
STATE_PATH = HERE / "output" / "tradexyz_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
INFO_URL = "https://api.hyperliquid.xyz/info"
PAGE = "https://data.wublock123.com/dashboard/hyperliquid"
TARGET = "TradeXYZ"
MAJOR_PCT = 40.0
LOOKBACK_DAYS = 45
HISTORY_DAYS = 40
HISTORY_LIMIT = 4
LARK_DAILY = "【实时日常报告】"
LARK_THRESHOLD = "【历史阈值警报】"
LARK_SPECIAL = "【特殊警报】"
LABELS = {
    "xyz": "TradeXYZ",
    "flx": "Felix",
    "vntl": "Ventuals",
    "hyna": "hyENA",
    "km": "Markets",
    "mkts": "Markets",
    "cash": "Dreamcash",
    "para": "Paragon",
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
    return env


def info(body: dict) -> object:
    data = json.dumps(body).encode("utf-8")
    last: Exception | None = None
    for attempt in range(4):
        req = urllib.request.Request(INFO_URL, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in (429, 500, 502, 503):
                raise
            time.sleep(1.2 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"Hyperliquid info 失败: {last}")


def list_coins() -> list[tuple[str, str]]:
    dexes = info({"type": "perpDexs"})
    coins: list[tuple[str, str]] = []
    if not isinstance(dexes, list):
        return coins
    for dex in dexes:
        if not isinstance(dex, dict) or not dex.get("name"):
            continue
        name = str(dex["name"])
        label = LABELS.get(name, str(dex.get("fullName") or name))
        payload = info({"type": "metaAndAssetCtxs", "dex": name})
        if not isinstance(payload, list) or len(payload) < 2:
            continue
        meta = payload[0]
        universe = meta.get("universe") if isinstance(meta, dict) else None
        if not isinstance(universe, list):
            continue
        for asset in universe:
            if isinstance(asset, dict) and asset.get("name"):
                coins.append((str(asset["name"]), label))
        time.sleep(0.05)
    return coins


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def save_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def num(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def fetch_days(start: datetime, end: datetime) -> dict[str, dict[str, float]]:
    """已结束 UTC 日 → {市场: 成交额}。end 当天不计入。"""
    coins = list_coins()
    if not coins:
        raise RuntimeError("没有读到 HIP-3 市场")
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    acc: dict[str, dict[str, float]] = {}
    for index, (coin, label) in enumerate(coins, 1):
        try:
            candles = info({
                "type": "candleSnapshot",
                "req": {"coin": coin, "interval": "1d", "startTime": start_ms, "endTime": end_ms},
            })
        except (urllib.error.URLError, RuntimeError, TimeoutError):
            continue
        if isinstance(candles, list):
            for candle in candles:
                if not isinstance(candle, dict):
                    continue
                opened = datetime.fromtimestamp(int(candle["t"]) / 1000, timezone.utc).date()
                if opened < start.date() or opened >= end.date():
                    continue
                volume = num(candle.get("v")) * num(candle.get("c"))
                if volume <= 0:
                    continue
                day = opened.isoformat()
                bucket = acc.setdefault(day, {})
                bucket[label] = bucket.get(label, 0.0) + volume
        if index % 40 == 0:
            print(f"  candles {index}/{len(coins)}", flush=True)
        time.sleep(0.06)
    return acc


def series(today: datetime) -> dict[str, dict[str, float]]:
    end = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)
    cache = load_json(CACHE_PATH)
    days = cache.get("days") if isinstance(cache.get("days"), dict) else {}
    have = {day for day, row in days.items() if isinstance(row, dict) and num(row.get(TARGET)) > 0}
    needed = []
    cursor = start
    refresh_from = (end - timedelta(days=2)).date().isoformat()
    while cursor < end:
        key = cursor.date().isoformat()
        if key not in have or key >= refresh_from:
            needed.append(key)
        cursor += timedelta(days=1)
    if needed:
        fetch_start = datetime.fromisoformat(min(needed)).replace(tzinfo=timezone.utc)
        print(f"[tradexyz] fetch {fetch_start.date()} → {end.date()} exclusive", flush=True)
        fresh = fetch_days(fetch_start, end)
        for day, row in fresh.items():
            days[day] = row
        save_json(CACHE_PATH, {"updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "days": days})
    out: dict[str, dict[str, float]] = {}
    for day, row in days.items():
        if not isinstance(row, dict):
            continue
        if day < start.date().isoformat() or day >= end.date().isoformat():
            continue
        out[day] = {str(k): num(v) for k, v in row.items()}
    return out


def fmt_yi(n: float) -> str:
    return f"{n / 1e8:.2f} 亿美元"


def fmt_pct(n: float) -> str:
    return f"{'+' if n > 0 else ''}{n:.1f}%"


def view_of(days: dict[str, dict[str, float]], ordered: list[str], day: str) -> dict[str, Any]:
    idx = ordered.index(day)
    prev = ordered[idx - 1] if idx else None
    row = days[day]
    total = sum(row.values())
    xyz = row.get(TARGET, 0.0)
    share = xyz / total * 100 if total else 0.0
    dod = None
    if prev and days[prev].get(TARGET, 0) > 0:
        dod = (xyz / days[prev][TARGET] - 1) * 100
    return {"day": day, "xyz": xyz, "hip3": total, "share": share, "prev": prev, "dod": dod}


def build_daily(view: dict[str, Any]) -> str:
    dod = view["dod"]
    dod_s = f"，较前一日（{view['prev']}）{fmt_pct(float(dod))}" if isinstance(dod, float) else ""
    title = f"TradeXYZ 日成交 {fmt_yi(view['xyz'])}"
    if isinstance(dod, float):
        title += f"，环比 {fmt_pct(dod)}"
    body = (
        f"据吴说数据中心统计，{view['day']}（UTC）TradeXYZ 永续成交 {fmt_yi(view['xyz'])}{dod_s}，"
        f"约占当天 HIP-3 各市场合计 {fmt_yi(view['hip3'])} 的 {view['share']:.1f}%。"
        "成交额来自 Hyperliquid 官方日 K（成交量×收盘价），不是 HRC 季报原表。"
        f"\n{PAGE}"
    )
    return f"{title}\n{body}"


def build_threshold(view: dict[str, Any], *, historical: bool) -> str:
    dod = float(view["dod"])
    body = (
        f"【特殊】TradeXYZ 成交异动：{view['day']} 环比 {fmt_pct(dod)}\n"
        f"吴说获悉，据吴说数据中心统计，{view['day']}（UTC）TradeXYZ 永续成交 {fmt_yi(view['xyz'])}，"
        f"较前一日（{view['prev']}）{fmt_pct(dod)}，绝对值达到 {MAJOR_PCT:g}%，"
        f"约占当天 HIP-3 的 {view['share']:.1f}%。本条不是每日例行稿。"
    )
    if historical:
        found = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
        body += f"本条为历史回扫发现（交易日 {view['day']}，发现时间 {found} UTC+8），请勿当成刚收盘的一天。"
    body += f"\n{PAGE}"
    return body


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()).decode()
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    if raw and str(json.loads(raw).get("code", "0")) != "0":
        raise RuntimeError(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--history-scan", action="store_true")
    parser.add_argument("--no-daily", action="store_true")
    args = parser.parse_args()
    if getattr(args, "history_scan", False):
        args.history_scan = False
    today = datetime.now(timezone.utc)
    try:
        days = series(today)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}")
        return 1
    ordered = sorted(day for day, row in days.items() if row.get(TARGET, 0) > 0)
    if len(ordered) < 2:
        print("[ERROR] TradeXYZ 可用日期不足")
        return 1
    latest_day = ordered[-1]
    latest_date = datetime.fromisoformat(latest_day).replace(tzinfo=timezone.utc)
    if (today - latest_date).days > 3:
        print(f"[ERROR] 最新完整日 {latest_day} 过旧")
        return 1
    latest = view_of(days, ordered, latest_day)
    print(
        f"[tradexyz] latest={latest['day']} xyz={latest['xyz']:.0f} share={latest['share']:.1f} dod={latest['dod']}",
        flush=True,
    )
    briefs: list[dict[str, Any]] = []
    if isinstance(latest["dod"], float) and abs(latest["dod"]) >= MAJOR_PCT:
        briefs.append({
            "id": f"tradexyz-threshold-{latest['day']}",
            "kind": "threshold",
            "text": build_threshold(latest, historical=False),
        })
    if not args.no_daily:
        briefs.append({
            "id": f"tradexyz-daily-{latest['day']}",
            "kind": "daily",
            "text": build_daily(latest),
        })
    state = load_json(STATE_PATH)
    sent = {x for x in state.get("sentIds", []) if isinstance(x, str)}
    if args.history_scan:
        n = 0
        for day in reversed(ordered[-HISTORY_DAYS:]):
            if day == latest_day or n >= HISTORY_LIMIT:
                continue
            view = view_of(days, ordered, day)
            if not isinstance(view["dod"], float) or abs(view["dod"]) < MAJOR_PCT:
                continue
            msg_id = f"tradexyz-threshold-{view['day']}"
            if msg_id in sent and not args.force:
                continue
            briefs.append({
                "id": msg_id,
                "kind": "threshold",
                "text": build_threshold(view, historical=True),
            })
            n += 1
    seen: set[str] = set()
    unique = []
    for brief in sorted(briefs, key=lambda item: (0 if item["kind"] == "threshold" else 1, item["id"])):
        if brief["id"] in seen:
            continue
        seen.add(brief["id"])
        unique.append(brief)
    for brief in unique:
        mark = f"{LARK_THRESHOLD}\n{LARK_SPECIAL}" if brief["kind"] == "threshold" else LARK_DAILY
        print("---")
        print(brief["id"], brief["id"] in sent)
        print(f"{mark}\n{brief['text']}")
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0
    env = merge_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK")
        return 2
    sent_ids = [x for x in state.get("sentIds", []) if isinstance(x, str)]
    sent_any = False
    for brief in unique:
        if brief["id"] in sent_ids and not args.force:
            print("[skip]", brief["id"])
            continue
        mark = f"{LARK_THRESHOLD}\n{LARK_SPECIAL}" if brief["kind"] == "threshold" else LARK_DAILY
        try:
            send_lark(webhook, secret, f"{mark}\n{brief['text']}")
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] {brief['id']}: {exc}")
            return 1
        print("[pushed]", brief["id"])
        sent_ids.append(brief["id"])
        sent_any = True
        time.sleep(0.4)
    if sent_any:
        save_json(STATE_PATH, {"sentIds": sent_ids[-200:], "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
