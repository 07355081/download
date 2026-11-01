# -*- coding: utf-8 -*-
"""美现货比特币 ETF 日成交额 → Lark。

只发最新一个已完成美股交易日，不回扫历史。
- 日常：【实时日常报告】+ 可发稿新闻正文
- 阈值：30 日均相对本序列峰值回撤首次跌破 -50%，或创出更差回撤 →【历史阈值警报】

用法：
  python flash.py
  python flash.py --push
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    HERE / "output" / "json" / "btc_etf_volume.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "btc-etf-volume" / "btc_etf_volume.json",
    Path("/root/dashboard/public/json/btc-etf-volume/btc_etf_volume.json"),
]
STATE_PATH = HERE / "output" / "btc_etf_volume_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
PAGE = "https://data.wublock123.com/dashboard/etf/btc"
ETF_PAGE = "https://data.wublock123.com/dashboard/etf/btc"
LARK_DAILY = "【实时日常报告】"
LARK_THRESHOLD = "【历史阈值警报】"
DEFAULT_DROP_PCT = -50.0


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
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET", "BTC_ETF_VOLUME_DROP_PCT"):
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


def resolve_json(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 btc_etf_volume.json，请先跑 download.py")


def load_rows(path: Path) -> list[dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("invalid json")
    out = [row for row in rows if isinstance(row, dict) and row.get("time")]
    out.sort(key=lambda row: str(row["time"]))
    return out


def fmt_yi(n: float) -> str:
    a = abs(n) / 1e8
    if a >= 100:
        return f"{a:.0f} 亿美元"
    if a >= 10:
        return f"{a:.1f} 亿美元"
    return f"{a:.2f} 亿美元"


def fmt_pct(n: float) -> str:
    sign = "+" if n > 0 else ""
    return f"{sign}{n:.1f}%"


def cn_day(day: str) -> str:
    y, m, d = day.split("-")
    return f"{int(y)} 年 {int(m)} 月 {int(d)} 日"


def top_tickers(row: dict[str, Any], n: int = 3) -> str:
    by = row.get("by_ticker")
    if not isinstance(by, dict):
        return ""
    items = [(k, float(v)) for k, v in by.items() if isinstance(v, (int, float))]
    items.sort(key=lambda x: x[1], reverse=True)
    if not items:
        return ""
    parts = [f"{k} 约 {fmt_yi(v)}" for k, v in items[:n]]
    return "成交额前三为 " + "、".join(parts) + "。"


def build_text(row: dict[str, Any]) -> str:
    day = str(row["time"])
    vol = float(row["volume_usd"])
    ma = row.get("ma30_usd")
    vs = row.get("vs_peak_pct")
    bits = [f"美国现货比特币 ETF 合计日成交约 {fmt_yi(vol)}"]
    if isinstance(ma, (int, float)):
        bits.append(f"近 30 日均约 {fmt_yi(float(ma))}")
    if isinstance(vs, (int, float)):
        bits.append(f"较本序列 30 日均峰值 {fmt_pct(float(vs))}")
    body = "，".join(bits) + "。"
    top = top_tickers(row)
    return (
        f"据吴说数据中心统计，截至 {cn_day(day)}，{body}{top}"
        "口径为 Yahoo Finance 各基金日成交量乘以收盘价后加总，非 Glassnode。"
        f"\n\n{ETF_PAGE}"
    )


def should_threshold(rows: list[dict[str, Any]], line: float) -> bool:
    if not rows:
        return False
    latest = rows[-1]
    vs = latest.get("vs_peak_pct")
    if not isinstance(vs, (int, float)) or float(vs) > line:
        return False
    prev = rows[-2] if len(rows) >= 2 else None
    prev_vs = prev.get("vs_peak_pct") if isinstance(prev, dict) else None
    crossed = not isinstance(prev_vs, (int, float)) or float(prev_vs) > line
    prior = [
        float(r["vs_peak_pct"])
        for r in rows[:-1]
        if isinstance(r.get("vs_peak_pct"), (int, float))
    ]
    new_low = bool(prior) and float(vs) < min(prior) - 1e-9
    return crossed or new_low


def gen_lark_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark_text(webhook: str, secret: str | None, text: str) -> None:
    body = as_news(text)
    for mark in (LARK_THRESHOLD, LARK_DAILY):
        if text.startswith(mark):
            body = f"{mark}\n{body}"
            break
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
        raw = resp.read().decode("utf-8", errors="replace")
    if raw:
        result = json.loads(raw)
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
    return {"sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else []}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", default=None)
    args = parser.parse_args()
    env = merge_env()
    line = env_float(env, "BTC_ETF_VOLUME_DROP_PCT", DEFAULT_DROP_PCT)
    rows = load_rows(resolve_json(args.json))
    if not rows:
        print("[btc-etf-volume] 无数据")
        return 0
    latest = rows[-1]
    day = str(latest["time"])
    if not isinstance(latest.get("volume_usd"), (int, float)):
        print(f"[btc-etf-volume] {day} 无成交额")
        return 0

    briefs: list[dict[str, str]] = [
        {"id": f"btc-etf-volume-daily-{day}", "mark": LARK_DAILY, "text": build_text(latest)}
    ]
    if should_threshold(rows, line):
        briefs.append(
            {
                "id": f"btc-etf-volume-threshold-{day}",
                "mark": LARK_THRESHOLD,
                "text": build_text(latest),
            }
        )

    state = load_state()
    sent = set(state["sentIds"])
    for brief in briefs:
        text = f"{brief['mark']}\n{brief['text']}"
        print("---")
        print(f"id={brief['id']} already={brief['id'] in sent}")
        print(text)

    if not args.push:
        print("\n[dry-run] 未推送")
        return 0

    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2

    sent_any = False
    for brief in briefs:
        if brief["id"] in sent and not args.force:
            print(f"[skip] already {brief['id']}")
            continue
        text = f"{brief['mark']}\n{brief['text']}"
        try:
            send_lark_text(webhook, secret, text)
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] Lark 失败 {brief['id']}: {exc}", file=sys.stderr)
            return 1
        print(f"[pushed] {brief['id']}")
        if brief["id"] not in state["sentIds"]:
            state["sentIds"].append(brief["id"])
        sent_any = True
        time.sleep(0.4)
    if sent_any:
        state["sentIds"] = state["sentIds"][-160:]
        save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
