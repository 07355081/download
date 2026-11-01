#!/usr/bin/env python3
"""大市值山寨月度 → Lark。

读 alt-largecap/monthly.json（与看板同一份）。
- 最近一个已结束月：【实时日常报告】
- 更早月份若中位数或普涨过线：【历史阈值警报】+【特殊警报】，并写明历史回扫
最新月的过线警报仍由早上 8 点的看板任务发送，这里不重复发。

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
    Path("/root/dashboard/public/json/alt-largecap/monthly.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "alt-largecap" / "monthly.json",
    HERE / "output" / "json" / "monthly.json",
]
STATE_PATH = HERE / "output" / "lark_state.json"
LOG_PATH = HERE / "flash.log"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
DASHBOARD_URL = "https://data.wublock123.com/dashboard/alt-largecap"
MARK_DAILY = "【实时日常报告】"
MARK_THRESHOLD = "【历史阈值警报】"
MARK_SPECIAL = "【特殊警报】"
HISTORY_LIMIT = 4


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
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, b"", hashlib.sha256).digest()
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
            if isinstance(doc, dict) and isinstance(doc.get("months"), list):
                print(f"[alt-largecap] json={path}")
                return doc
    raise FileNotFoundError("找不到 alt-largecap/monthly.json")


def num(row: dict[str, Any], key: str) -> float:
    value = row.get(key)
    return float(value) if isinstance(value, (int, float)) else float("nan")


def breached(row: dict[str, Any]) -> bool:
    n = num(row, "n_priced")
    if not n or n != n or n < 40:
        return False
    median = num(row, "median_pct")
    up = num(row, "n_up") / n
    beat = num(row, "n_beat_btc") / n
    hot = median >= 15
    broad = up >= 0.7 and beat >= 0.55 and median >= 8
    return hot or broad


def pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def month_zh(label: str) -> str:
    year, month = label.split("-")
    return f"{int(year)}年{int(month)}月"


def leaders_text(row: dict[str, Any]) -> str:
    leaders = row.get("leaders") if isinstance(row.get("leaders"), list) else []
    bits = []
    for item in leaders[:3]:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").upper()
        value = item.get("pct")
        if symbol and isinstance(value, (int, float)):
            bits.append(f"{symbol} {pct(float(value))}")
    return "、".join(bits)


def body(row: dict[str, Any], *, historical: bool) -> str:
    label = str(row["month"])
    n = int(num(row, "n_priced"))
    text = (
        f"据吴说数据中心统计，{month_zh(label)}，按当前大市值山寨计算，"
        f"有价格的 {n} 个里 {int(num(row, 'n_up'))} 个上涨，"
        f"{int(num(row, 'n_beat_btc'))} 个跑赢比特币。"
        f"比特币当月约 {pct(num(row, 'btc_pct'))}。"
        f"这批币平均涨幅约 {pct(num(row, 'avg_pct'))}，中位数约 {pct(num(row, 'median_pct'))}。"
    )
    names = leaders_text(row)
    if names:
        text += f"涨幅靠前的是 {names}。"
    text += "样本是抓取时 CoinGecko 市值前 100，去掉比特币和稳定币；更早月份用同一份名单回算。涨跌从该月 1 日到下月 1 日（UTC）。"
    if historical:
        found = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
        text += f"本条为历史回扫（发现时间 {found} UTC+8），请勿当成刚结束的月份。"
    return f"{text}\n{DASHBOARD_URL}"


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

    months = [row for row in doc["months"] if isinstance(row, dict) and isinstance(row.get("month"), str)]
    if not months:
        print("[skip] 没有月份")
        return 0
    latest = months[-1]
    briefs: list[dict[str, str]] = []
    if not args.no_daily:
        briefs.append({
            "id": f"alt-largecap-daily-{latest['month']}",
            "text": f"{MARK_DAILY}\n{body(latest, historical=False)}",
        })
    if args.history_scan:
        found = 0
        for row in reversed(months[:-1]):
            if found >= HISTORY_LIMIT:
                break
            if not breached(row):
                continue
            briefs.append({
                "id": f"alt-largecap-threshold-{row['month']}",
                "text": f"{MARK_THRESHOLD}\n{MARK_SPECIAL}\n{body(row, historical=True)}",
            })
            found += 1

    state = load_state()
    for brief in briefs:
        print("---")
        print(f"id={brief['id']} already={brief['id'] in state['sentIds']}")
        print(brief["text"])
    if not briefs:
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
    for brief in briefs:
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
