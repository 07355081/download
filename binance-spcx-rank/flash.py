#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""币安 SPCX 永续成交排名 → Lark 新闻。

只发最新过线事件，不回扫历史：
- SPCXUSDT 24h 成交进入币安 U 本位前 3 →【历史阈值警报】
- 最近一个已收盘 UTC 日成交较前一日 ≥ 阈值（默认 +200%）→【历史阈值警报】
同一条件同一天只发一次。正文新闻体，可直接发稿。

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
    HERE / "output" / "json" / "binance_spcx_rank.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "binance-spcx-rank" / "binance_spcx_rank.json",
    Path("/root/dashboard/public/json/binance-spcx-rank/binance_spcx_rank.json"),
]
STATE_PATH = HERE / "output" / "binance_spcx_rank_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
PAGE = "https://data.wublock123.com/dashboard/tradfi"
JSON_PAGE = "https://data.wublock123.com/dashboard/tradfi"
LARK_THRESHOLD = "【历史阈值警报】"
DEFAULT_DOD_PCT = 200.0
RANK_LINE = 3
TZ_CN = timezone(timedelta(hours=8))

sys.path.insert(0, str(ROOT))
from lark_copy import as_news  # noqa: E402


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
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET", "BINANCE_SPCX_DOD_PCT"):
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
    raise FileNotFoundError("找不到 binance_spcx_rank.json，请先跑 download.py")


def fmt_yi(n: float) -> str:
    a = abs(n) / 1e8
    if a >= 100:
        return f"{a:.0f} 亿美元"
    if a >= 10:
        return f"{a:.1f} 亿美元"
    return f"{a:.2f} 亿美元"


def fmt_pct(n: float) -> str:
    sign = "+" if n > 0 else ""
    return f"{sign}{n:.0f}%"


def cn_now() -> str:
    now = datetime.now(TZ_CN)
    return f"{now.year} 年 {now.month} 月 {now.day} 日 {now.hour:02d}:{now.minute:02d}"


def cn_day(day: str) -> str:
    y, m, d = day.split("-")
    return f"{int(y)} 年 {int(m)} 月 {int(d)} 日"


def short_sym(sym: str) -> str:
    s = str(sym or "")
    return s[:-4] if s.endswith("USDT") else s


def top_line(top: list[dict[str, Any]], n: int = 5) -> str:
    parts = []
    for row in top[:n]:
        if not isinstance(row, dict):
            continue
        parts.append(f"{short_sym(str(row.get('symbol')))} 约 {fmt_yi(float(row.get('volume_usd') or 0))}")
    return "、".join(parts)


def build_rank_text(doc: dict[str, Any]) -> str:
    spcx = doc.get("spcx") if isinstance(doc.get("spcx"), dict) else {}
    top = doc.get("top") if isinstance(doc.get("top"), list) else []
    rank = int(spcx.get("rank") or 0)
    vol = float(spcx.get("volume_usd_24h") or 0)
    ahead = spcx.get("ahead_symbols") if isinstance(spcx.get("ahead_symbols"), list) else []
    ahead_txt = "、".join(short_sym(str(x)) for x in ahead) if ahead else "—"
    only = bool(spcx.get("only_behind_btc_eth"))
    extra = "目前仅次于 BTC 与 ETH。" if only else f"成交额更高的交易对为 {ahead_txt}。"
    return (
        f"据吴说数据中心统计，截至北京时间 {cn_now()}，币安 U 本位永续 SPCXUSDT 近 24 小时成交约 {fmt_yi(vol)}，"
        f"在全部 USDT 永续交易对中排名第 {rank}。{extra}"
        f"当前成交额前五为 {top_line(top)}。"
        "口径为币安官方 24 小时成交额，非 CoinGlass 页面。"
        f"\n\n{PAGE}"
    )


def build_dod_text(doc: dict[str, Any]) -> str:
    daily = doc.get("daily") if isinstance(doc.get("daily"), dict) else {}
    last = daily.get("last_complete") if isinstance(daily.get("last_complete"), dict) else {}
    prev = daily.get("prev_complete") if isinstance(daily.get("prev_complete"), dict) else {}
    dod = float(daily.get("day_dod_pct") or 0)
    day = str(last.get("time") or "")
    vol = float(last.get("volume_usd") or 0)
    prev_vol = float(prev.get("volume_usd") or 0)
    spcx = doc.get("spcx") if isinstance(doc.get("spcx"), dict) else {}
    rank = int(spcx.get("rank") or 0)
    return (
        f"据吴说数据中心统计，{cn_day(day)}（UTC 日）币安 U 本位永续 SPCXUSDT 成交约 {fmt_yi(vol)}，"
        f"较前一完整交易日约 {fmt_yi(prev_vol)} 变化 {fmt_pct(dod)}。"
        f"截至推送时近 24 小时成交排名第 {rank}。"
        "口径为币安官方日 K 成交额，非 CoinGlass 页面。"
        f"\n\n{PAGE}"
    )


def gen_lark_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark_text(webhook: str, secret: str | None, text: str) -> None:
    body = as_news(text)
    if text.startswith(LARK_THRESHOLD):
        body = f"{LARK_THRESHOLD}\n{body}"
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
    dod_line = env_float(env, "BINANCE_SPCX_DOD_PCT", DEFAULT_DOD_PCT)
    doc = json.loads(resolve_json(args.json).read_text(encoding="utf-8"))
    spcx = doc.get("spcx") if isinstance(doc.get("spcx"), dict) else {}
    daily = doc.get("daily") if isinstance(doc.get("daily"), dict) else {}
    as_of = str((doc.get("meta") or {}).get("as_of_utc_day") or datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    rank = int(spcx.get("rank") or 0)
    dod = daily.get("day_dod_pct")
    last_day = ""
    if isinstance(daily.get("last_complete"), dict):
        last_day = str(daily["last_complete"].get("time") or "")

    briefs: list[dict[str, str]] = []
    if rank and rank <= RANK_LINE:
        briefs.append(
            {
                "id": f"binance-spcx-rank-top{RANK_LINE}-{as_of}",
                "text": f"{LARK_THRESHOLD}\n{build_rank_text(doc)}",
            }
        )
    if isinstance(dod, (int, float)) and float(dod) >= dod_line and last_day:
        briefs.append(
            {
                "id": f"binance-spcx-rank-dod-{last_day}",
                "text": f"{LARK_THRESHOLD}\n{build_dod_text(doc)}",
            }
        )

    if not briefs:
        print(
            f"[binance-spcx-rank] 未过线 rank={rank} dod={dod} "
            f"(需 rank≤{RANK_LINE} 或日环比≥{dod_line}%)"
        )
        return 0

    state = load_state()
    sent = set(state["sentIds"])
    for brief in briefs:
        print("---")
        print(f"id={brief['id']} already={brief['id'] in sent}")
        print(brief["text"])

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
        try:
            send_lark_text(webhook, secret, brief["text"])
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
