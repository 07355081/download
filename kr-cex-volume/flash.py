# -*- coding: utf-8 -*-
"""Upbit / Bithumb 近 24 小时成交 → Lark。

只有上一采样日存在时才发。第一天只攒数，不发。只发最新采样，不回扫历史。
- 日常：【实时日常报告】+ 可发稿新闻正文
- 阈值：【历史阈值警报】任一交易所 |涨幅| ≥ 100%（首行标记；正文仍是新闻体）

不回扫外电里已经过去的涨幅。CoinGecko 接口没有那段历史。

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    HERE / "output" / "json" / "kr_cex_volume.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "kr-cex-volume" / "kr_cex_volume.json",
    Path("/root/dashboard/public/json/kr-cex-volume/kr_cex_volume.json"),
]
STATE_PATH = HERE / "output" / "kr_cex_volume_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
PAGE = "https://data.wublock123.com/dashboard/exchange_volume"
JSON_URL = "https://data.wublock123.com/dashboard/exchange_volume"
DEFAULT_DOD_PCT = 100.0
NAMES = (("upbit", "Upbit"), ("bithumb", "Bithumb"))


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
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET", "KR_CEX_VOLUME_DOD_PCT"):
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


def resolve_json_path(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 kr_cex_volume.json，请先跑 download.py")


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
    if a >= 10:
        return f"{a:.1f} 亿美元"
    return f"{a:.2f} 亿美元"


def fmt_pct(n: float) -> str:
    sign = "+" if n > 0 else ""
    return f"{sign}{n:.1f}%"


def cn_day(day: str) -> str:
    y, m, d = day.split("-")
    return f"{int(y)} 年 {int(m)} 月 {int(d)} 日"


def line_for(row: dict[str, Any], key: str, label: str) -> str | None:
    usd = row.get(f"{key}_volume_usd")
    dod = row.get(f"{key}_dod_pct")
    if not isinstance(usd, (int, float)) or not isinstance(dod, (int, float)):
        return None
    return f"{label} 近 24 小时成交约 {fmt_yi(float(usd))}，较上一采样日 {fmt_pct(float(dod))}"


def build_text(row: dict[str, Any], *, threshold: bool, line: float) -> str:
    # threshold/line 仅保留调用签名；正文不写警报说明，分类靠首行标记
    _ = (threshold, line)
    day = str(row["time"])
    bits = [line_for(row, key, label) for key, label in NAMES]
    bits = [bit for bit in bits if bit]
    body = "；".join(bits) + "。"
    return (
        f"据吴说数据中心统计，截至 {cn_day(day)} 采样，{body}"
        "口径为 CoinGecko 近 24 小时 BTC 成交量乘以采样时比特币美元价，涨幅相对上一采样日。"
        f"\n\n{PAGE}"
    )


def hits_threshold(row: dict[str, Any], line: float) -> bool:
    for key, _label in NAMES:
        dod = row.get(f"{key}_dod_pct")
        if isinstance(dod, (int, float)) and abs(float(dod)) >= line:
            return True
    return False


def has_change(row: dict[str, Any]) -> bool:
    return any(isinstance(row.get(f"{key}_dod_pct"), (int, float)) for key, _label in NAMES)


def gen_lark_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark_text(webhook: str, secret: str | None, text: str) -> None:
    body = as_news(text)
    for mark in (LARK_MARK_THRESHOLD, LARK_MARK_DAILY):
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
    line = env_float(env, "KR_CEX_VOLUME_DOD_PCT", DEFAULT_DOD_PCT)
    rows = load_rows(resolve_json_path(args.json))
    if not rows:
        print("[kr-cex-volume] 无数据")
        return 0
    latest = rows[-1]
    day = str(latest["time"])
    if not has_change(latest):
        print(f"[kr-cex-volume] {day} 还没有上一采样日，只存不发")
        return 0

    briefs: list[dict[str, str]] = [
        {"id": f"kr-cex-volume-daily-{day}", "mark": LARK_MARK_DAILY, "text": build_text(latest, threshold=False, line=line)}
    ]
    if hits_threshold(latest, line):
        briefs.append(
            {
                "id": f"kr-cex-volume-threshold-{day}",
                "mark": LARK_MARK_THRESHOLD,
                "text": build_text(latest, threshold=True, line=line),
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
