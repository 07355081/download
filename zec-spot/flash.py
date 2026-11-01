#!/usr/bin/env python3
"""ZEC → Lark。

- 日常：OKX 现价、24 小时涨跌、30/90 日涨跌，加上 CoinGlass 市值、合约成交、未平仓、爆仓
- 当时过线：24 小时涨跌绝对值达到 20%，或 24 小时爆仓达到 3000 万美元
- --history-scan：更早的日线涨跌达到 20%，或盘中升破 1000 美元，加【特殊警报】
  历史条只有 OKX 价格，不补市值和爆仓

用法：
  python flash.py
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
    HERE / "output" / "json" / "zec_spot.json",
    Path("/root/dashboard/public/json/zec-spot/zec_spot.json"),
]
STATE_PATH = HERE / "output" / "lark_state.json"
LOG_PATH = HERE / "flash.log"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
MARK_DAILY = "【实时日常报告】"
MARK_THRESHOLD = "【历史阈值警报】"
MARK_SPECIAL = "【特殊警报】"
MOVE_PCT = 20.0
LIQ_USD = 30_000_000.0
HISTORY_LIMIT = 4
JSON_URL = "https://data.wublock123.com/dashboard/lark-feeds/zec-spot"


PAGE = "https://data.wublock123.com/dashboard/lark-feeds/zec-spot"
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
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


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
        return {"sentIds": []}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": []}
    ids = doc.get("sentIds") if isinstance(doc, dict) else None
    return {"sentIds": [item for item in ids if isinstance(item, str)] if isinstance(ids, list) else []}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def append_log(stamp: str, msg_id: str, ok: bool, text: str) -> None:
    line = json.dumps({"ts": stamp, "id": msg_id, "ok": ok, "text": text}, ensure_ascii=False)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def load_doc() -> dict[str, Any]:
    for path in JSON_CANDIDATES:
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("live"), dict):
                print(f"[zec-spot] json={path}")
                return doc
    raise FileNotFoundError("找不到 zec_spot.json")


def usd_cn(value: float) -> str:
    if value >= 1e8:
        return f"{value / 1e8:.2f} 亿美元"
    if value >= 1e4:
        return f"{value / 1e4:.0f} 万美元"
    return f"{value:.0f} 美元"


def pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def daily_text(doc: dict[str, Any]) -> str:
    live = doc["live"]
    r30 = live["ret_30d"]
    r90 = live["ret_90d"]
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
    return (
        f"据吴说数据中心统计，截至 {stamp}（UTC+8），OKX 现货 ZEC-USDT 约 {live['okx_price']:.2f} 美元，"
        f"过去 24 小时约 {pct(float(live['chg_24h_pct']))}。"
        f"CoinGlass 口径市值约 {usd_cn(float(live['market_cap_usd']))}，"
        f"24 小时合约成交约 {usd_cn(float(live['volume_usd_24h']))}，"
        f"未平仓约 {usd_cn(float(live['open_interest_usd']))}，"
        f"24 小时爆仓约 {usd_cn(float(live['liquidation_usd_24h']))}"
        f"（多单约 {usd_cn(float(live['long_liquidation_usd_24h']))}，"
        f"空单约 {usd_cn(float(live['short_liquidation_usd_24h']))}）。"
        f"近 30 日约 {pct(float(r30['pct']))}（自 {r30['from_day']} 收盘 {r30['from_close']:.2f} 美元），"
        f"近 90 日约 {pct(float(r90['pct']))}（自 {r90['from_day']} 收盘 {r90['from_close']:.2f} 美元）。"
        f"\n{PAGE}"
    )


def history_text(row: dict[str, Any]) -> str:
    found = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
    chg = row.get("chg_pct")
    chg_bit = f"较前一日收盘约 {pct(float(chg))}。" if isinstance(chg, (int, float)) else ""
    cross = ""
    if row.get("cross_1000"):
        cross = "当天盘中价格从 1000 美元下方升到上方。"
    return (
        f"据吴说数据中心统计，{row['day']} OKX 现货 ZEC 开盘约 {row['open']:.2f} 美元，"
        f"最高约 {row['high']:.2f} 美元，收盘约 {row['close']:.2f} 美元。{chg_bit}{cross}"
        f"这一天的市值、合约成交和爆仓没有留下快照。"
        f"本条为历史回扫（发现时间 {found} UTC+8），请勿当成刚刚发生。"
        f"\n{PAGE}"
    )


def live_threshold(doc: dict[str, Any]) -> str | None:
    live = doc["live"]
    bits = []
    chg = float(live["chg_24h_pct"])
    liq = float(live["liquidation_usd_24h"])
    if abs(chg) >= MOVE_PCT:
        bits.append(f"过去 24 小时约 {pct(chg)}")
    if liq >= LIQ_USD:
        bits.append(f"24 小时爆仓约 {usd_cn(liq)}")
    if not bits:
        return None
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
    return (
        f"据吴说数据中心统计，截至 {stamp}（UTC+8），ZEC {'，'.join(bits)}。"
        f"OKX 现货约 {float(live['okx_price']):.2f} 美元。"
        f"CoinGlass 24 小时合约成交约 {usd_cn(float(live['volume_usd_24h']))}，"
        f"未平仓约 {usd_cn(float(live['open_interest_usd']))}，"
        f"爆仓约 {usd_cn(liq)}（多单约 {usd_cn(float(live['long_liquidation_usd_24h']))}，"
        f"空单约 {usd_cn(float(live['short_liquidation_usd_24h']))}）。"
        f"\n{PAGE}"
    )


def history_events(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flagged = []
    for row in days:
        chg = row.get("chg_pct")
        if row.get("cross_1000") or (isinstance(chg, (int, float)) and abs(float(chg)) >= MOVE_PCT):
            flagged.append(row)
    by_day = {row["day"]: row for row in flagged}
    chosen = []
    for row in reversed(flagged):
        nxt = (datetime.strptime(row["day"], "%Y-%m-%d").date() + timedelta(days=1)).isoformat()
        if nxt in by_day:
            continue
        chosen.append(row)
        if len(chosen) >= HISTORY_LIMIT:
            break
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--history-scan", action="store_true")
    parser.add_argument("--no-daily", action="store_true")
    args = parser.parse_args()
    if getattr(args, "history_scan", False):
        args.history_scan = False
    try:
        doc = load_doc()
    except (OSError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    day = datetime.now(TZ_CN).strftime("%Y-%m-%d")
    briefs: list[dict[str, str]] = []
    if not args.no_daily:
        briefs.append({"id": f"zec-daily-{day}", "text": f"{MARK_DAILY}\n{daily_text(doc)}"})
    threshold = live_threshold(doc)
    if threshold:
        briefs.append({"id": f"zec-threshold-{day}", "text": f"{MARK_THRESHOLD}\n{threshold}"})
    else:
        print(f"[info] 当前 24 小时涨跌未到 {MOVE_PCT:.0f}%，爆仓未到 3000 万美元")
    if args.history_scan:
        for row in history_events(doc.get("days") or []):
            kind = "cross1000" if row.get("cross_1000") else "move20"
            briefs.append({
                "id": f"zec-hist-{kind}-{row['day']}",
                "text": f"{MARK_THRESHOLD}\n{MARK_SPECIAL}\n{history_text(row)}",
            })

    state = load_state()
    seen: set[str] = set()
    unique = []
    for brief in briefs:
        if brief["id"] in seen:
            continue
        seen.add(brief["id"])
        unique.append(brief)
    for brief in unique:
        print("---")
        print(f"id={brief['id']} already={brief['id'] in state['sentIds']}")
        print(brief["text"])
    if not unique:
        print("[skip] 没有可发内容")
        return 0
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0

    env = merge_env()
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
        try:
            send_lark_text(webhook, secret, brief["text"])
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] Lark 失败 {brief['id']}: {exc}", file=sys.stderr)
            append_log(stamp, brief["id"], False, brief["text"])
            return 1
        print(f"[pushed] {brief['id']}")
        if brief["id"] not in state["sentIds"]:
            state["sentIds"].append(brief["id"])
        sent_any = True
        append_log(stamp, brief["id"], True, brief["text"])
        time.sleep(0.4)
    if sent_any:
        state["sentIds"] = state["sentIds"][-200:]
        save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
