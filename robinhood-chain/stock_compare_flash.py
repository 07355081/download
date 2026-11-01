"""股票代币成交对比 → Lark。数据来自 stock_compare_download.py，只读 Dune 缓存。

- 【实时日常报告】每个上海日一条
- 【历史阈值警报】Robinhood 30 日成交至少为 Base 的 2 倍，或任一侧最新日 ≥ 3 亿美元
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_PATH = HERE / "output" / "json" / "stock_token_compare.json"
STATE_PATH = HERE / "output" / "stock_compare_lark.json"
TZ_CN = timezone(timedelta(hours=8))
PAGE = "https://data.wublock123.com/dashboard/robinhood-chain"
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
RATIO_MIN = 2.0
DAY_MIN = 3e8
ENV_FILES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
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

def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in ENV_FILES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def fmt_usd(value: float) -> str:
    if value >= 1e8:
        return f"{value / 1e8:.2f} 亿美元"
    return f"{value / 1e4:,.0f} 万美元"


def build(doc: dict[str, Any]) -> tuple[str, list[str]]:
    rh = doc["robinhood"]
    base = doc["coinbase_base"]
    rh_30 = float(rh["volume_30d_usd"])
    base_30 = float(base["volume_30d_usd"])
    reasons: list[str] = []
    if base_30 > 0 and rh_30 >= base_30 * RATIO_MIN:
        reasons.append(f"Robinhood 30 日成交至少为 Base 的 {RATIO_MIN:g} 倍")
    if float(rh["latest_day_usd"]) >= DAY_MIN:
        reasons.append(f"Robinhood 最新日 {rh['latest_day']} 成交达到 {fmt_usd(float(rh['latest_day_usd']))}")
    if float(base["latest_day_usd"]) >= DAY_MIN:
        reasons.append(f"Base 最新日 {base['latest_day']} 成交达到 {fmt_usd(float(base['latest_day_usd']))}")
    text = (
        f"据吴说数据中心统计（Dune 缓存近似，不是 Token Terminal），"
        f"{doc['window_start']} 至 {doc['window_end']}，"
        f"Robinhood Chain 股票现货成交约 {fmt_usd(rh_30)}，"
        f"Coinbase 在 Base 发行的股票代币 DEX 成交约 {fmt_usd(base_30)}。"
        f"Robinhood 最新完整日（{rh['latest_day']}）约 {fmt_usd(float(rh['latest_day_usd']))}，"
        f"Base 最新完整日（{base['latest_day']}）约 {fmt_usd(float(base['latest_day_usd']))}。"
        "口径为公开查询缓存：Robinhood 含场内与 DEX 的股票现货，Base 为 Coinbase 股票代币全 DEX。"
    )
    return f"{text}\n{PAGE}", reasons


def send_lark(webhook: str, secret: str | None, text: str) -> None:
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
        body = json.loads(resp.read().decode("utf-8"))
    if str(body.get("code", "0")) != "0":
        raise RuntimeError(body)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    if not JSON_PATH.is_file():
        print(f"[ERROR] 缺少 {JSON_PATH}", file=sys.stderr)
        return 1
    doc = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    text, reasons = build(doc)
    print(text)
    print("threshold", reasons)
    if not args.push:
        print("[dry-run]", file=sys.stderr)
        return 0
    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 1
    state: dict[str, Any] = {"daily": {}, "threshold": {}}
    if STATE_PATH.is_file():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    state.setdefault("daily", {})
    state.setdefault("threshold", {})
    today = datetime.now(TZ_CN).date().isoformat()
    if today not in state["daily"]:
        send_lark(webhook, secret, f"{LARK_MARK_DAILY}\n{text}")
        state["daily"][today] = True
        print("[pushed] daily", file=sys.stderr)
    key = "|".join(reasons)
    if reasons and not state["threshold"].get(key):
        send_lark(webhook, secret, f"{LARK_MARK_THRESHOLD}\n{'；'.join(reasons)}。\n{text}")
        state["threshold"][key] = True
        print("[pushed] threshold", file=sys.stderr)
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
