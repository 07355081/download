# -*- coding: utf-8 -*-
"""Deribit 最近到期的 BTC、ETH 期权 → Lark。

- 最新快照：【实时日常报告】
- 若网页那条同一天已经发出，不再发第二条日常
- 最近到期占持仓达到 15%，或总持仓较前一日增减达到 20%：【历史阈值警报】
- 总持仓或最近到期占比创历史新高：同一条再加【特殊警报】
- 只看最新一天，不回扫旧日期，不补推 9 月 4 日那篇
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
    HERE / "output" / "json" / "options_expiry.json",
    Path("/root/dashboard/public/json/options-expiry/options_expiry.json"),
]
STATE_PATH = HERE / "output" / "options_expiry_lark.json"
LOG_PATH = HERE / "options_expiry_flash.log"
PAGE_STATE = Path("/root/dashboard/data/options-delivery-lark.json")
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
DASHBOARD_URL = "https://data.wublock123.com/dashboard/options"
MIN_PRIOR = 30
SHARE_THRESHOLD = 0.15
MOVE_THRESHOLD = 0.20


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


def load_doc(explicit: str) -> tuple[Path, dict[str, Any]]:
    paths = [Path(explicit)] if explicit else JSON_CANDIDATES
    for path in paths:
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("history"), list):
                return path, doc
    raise FileNotFoundError("找不到 options_expiry.json")


def cn_day(day: str) -> str:
    year, month, date = day.split("-")
    return f"{year} 年 {int(month)} 月 {int(date)} 日"


def contracts(n: float) -> str:
    if abs(n) >= 10000:
        return f"{n / 10000:.2f} 万张"
    return f"{n:,.0f} 张"


def notional(n: int) -> str:
    if abs(n) >= 100_000_000:
        return f"{n / 1e8:.2f} 亿美元"
    if abs(n) >= 10_000:
        return f"{n / 1e4:.0f} 万美元"
    return f"{n:,} 美元"


def pct(n: float) -> str:
    return f"{n * 100:.1f}%"


def move_text(cur: float, prev: float) -> str:
    if prev == 0:
        return "无法比较"
    change = (cur - prev) / prev
    direction = "上升" if change > 0 else "下降" if change < 0 else "持平"
    return f"{direction} {abs(change) * 100:.1f}%"


def leg(name: str, row: dict[str, Any], prev: dict[str, Any] | None) -> str:
    pcr = row.get("pcr")
    pcr_text = "看涨仓位为 0，未计算 Put/Call Ratio" if pcr is None else f"Put/Call Ratio {float(pcr):.2f}"
    pain = row.get("max_pain")
    pain_text = "最大痛点暂缺" if not pain else f"最大痛点 {int(pain):,} 美元"
    share = row.get("share")
    share_text = "占持仓比例暂缺" if share is None else f"约占 Deribit {name} 期权持仓 {pct(float(share))}"
    oi = ""
    if prev is not None:
        oi = f"全部持仓较前一日{move_text(float(row['total_contracts']), float(prev['total_contracts']))}。"
    return (
        f"{cn_day(str(row['expiry']))}到期的 {name} 期权未平仓 {contracts(float(row['contracts']))}，"
        f"{pcr_text}，{pain_text}，名义价值约 {notional(int(row['notional']))}，{share_text}。{oi}"
    )


def core(row: dict[str, Any], prev: dict[str, Any] | None) -> str:
    btc_prev = None if prev is None else prev.get("btc")
    eth_prev = None if prev is None else prev.get("eth")
    return (
        f"据吴说数据中心统计，截至 {cn_day(str(row['time']))}，"
        f"{leg('BTC', row['btc'], btc_prev if isinstance(btc_prev, dict) else None)}"
        f"{leg('ETH', row['eth'], eth_prev if isinstance(eth_prev, dict) else None)}"
        f"\n{DASHBOARD_URL}"
    )


def prior_rows(rows: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    return [row for row in rows if str(row.get("time")) < day]


def is_new_high(rows: list[dict[str, Any]], day: str, asset: str, field: str) -> bool:
    prior = prior_rows(rows, day)
    if len(prior) < MIN_PRIOR:
        return False
    values = []
    for row in prior:
        asset_row = row.get(asset)
        if isinstance(asset_row, dict) and isinstance(asset_row.get(field), (int, float)):
            values.append(float(asset_row[field]))
    current = rows[-1].get(asset) if rows else None
    if not isinstance(current, dict) or not isinstance(current.get(field), (int, float)):
        return False
    if len(values) < MIN_PRIOR:
        return False
    return float(current[field]) > max(values)


def share_hit(row: dict[str, Any]) -> bool:
    for asset in ("btc", "eth"):
        asset_row = row.get(asset)
        if isinstance(asset_row, dict) and isinstance(asset_row.get("share"), (int, float)):
            if float(asset_row["share"]) >= SHARE_THRESHOLD:
                return True
    return False


def move_hit(row: dict[str, Any], prev: dict[str, Any] | None) -> bool:
    if prev is None:
        return False
    for asset in ("btc", "eth"):
        cur = row.get(asset)
        old = prev.get(asset)
        if not isinstance(cur, dict) or not isinstance(old, dict):
            continue
        before = float(old.get("total_contracts") or 0)
        after = float(cur.get("total_contracts") or 0)
        if before > 0 and abs(after - before) / before >= MOVE_THRESHOLD:
            return True
    return False


def shanghai_today() -> str:
    return datetime.now(TZ_CN).strftime("%Y-%m-%d")


def page_already_sent() -> bool:
    """网页日报按上海日期只发一次。同一天发过就不再发第二条日常。"""
    if not PAGE_STATE.is_file():
        return False
    try:
        doc = json.loads(PAGE_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    ids = doc.get("sentIds") if isinstance(doc, dict) else []
    return isinstance(ids, list) and f"options-delivery-{shanghai_today()}" in ids


def gen_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode("utf-8"), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = gen_sign(secret, ts)
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if str(body.get("code", "0")) != "0":
        raise RuntimeError(body)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": []}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": []}
    ids = doc.get("sentIds") if isinstance(doc, dict) else []
    return {"sentIds": [str(x) for x in ids] if isinstance(ids, list) else []}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    try:
        path, doc = load_doc(args.json)
        rows = [r for r in doc["history"] if isinstance(r, dict) and r.get("time")]
        rows.sort(key=lambda r: str(r["time"]))
        if not rows:
            raise RuntimeError("没有快照")
        row = rows[-1]
        prev = rows[-2] if len(rows) > 1 else None
    except (OSError, json.JSONDecodeError, FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    day = str(row["time"])
    age = (datetime.now(timezone.utc).date() - datetime.strptime(day, "%Y-%m-%d").date()).days
    print(f"[options-expiry] json={path} as_of={day} age={age}d")
    if age > 1:
        print("[ERROR] 最新期权快照超过 1 天，不发", file=sys.stderr)
        return 1

    body = core(row, prev)
    briefs: list[tuple[str, str, str]] = []
    record = any(is_new_high(rows, day, asset, field) for asset in ("btc", "eth") for field in ("total_contracts", "share"))
    if share_hit(row) or move_hit(row, prev) or record:
        mark = "【历史阈值警报】"
        text = "最近到期的占比或全部持仓变动，超过了日常波动。" + body
        if record:
            mark = "【历史阈值警报】\n【特殊警报】"
            text = "这是历史高点，不是日常新闻。" + text
        briefs.append(("threshold", f"options-expiry-threshold-{shanghai_today()}", f"{mark}\n{text}"))
    if not page_already_sent():
        briefs.append(("daily", f"options-expiry-daily-{shanghai_today()}", f"【实时日常报告】\n{body}"))
    else:
        print(f"[options-expiry] 网页日报已发过 {shanghai_today()}，跳过日常")

    state = load_state()
    for kind, msg_id, text in briefs:
        print("---")
        print(f"id={msg_id} kind={kind} already={msg_id in state['sentIds']}")
        print(text)
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
    for _kind, msg_id, text in briefs:
        if msg_id in state["sentIds"] and not args.force:
            print(f"skip {msg_id}")
            continue
        try:
            send_lark(webhook, secret, text)
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] Lark 失败 {msg_id}: {exc}", file=sys.stderr)
            return 1
        print(f"  → Lark OK {msg_id}")
        state["sentIds"].append(msg_id)
        sent_any = True
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"=== {stamp} id={msg_id}\n{text}\n\n")
        time.sleep(0.4)
    if sent_any:
        state["sentIds"] = state["sentIds"][-80:]
        save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
