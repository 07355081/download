# -*- coding: utf-8 -*-
"""币安传统资产永续：近 7 日成交，以及本月金属/股票/能源占比 → Lark 新闻。

只读已经镜像到网页的 Binance_*_1d.json，不新拉行情。
未收盘的当日 K 线不算。每个 ISO 周只发最新一段，不回扫。
全平台合约占比不在这套文件里，稿子不写。

用法：
  python binance_tradfi_share_flash.py
  python binance_tradfi_share_flash.py --push
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
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATE_PATH = HERE / "output" / "binance_tradfi_share_lark.json"
DIRS = [
    Path("/root/dashboard/public/json/tradfi-price"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "tradfi-price",
]
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
PAGE = "https://data.wublock123.com/dashboard/tradfi"
DAY_MS = 86_400_000
FRESH_DAYS = 3
METALS = {
    "XAU", "XAG", "XPT", "XPD", "XCU", "COPPER", "GOLD", "SILVER",
    "PLATINUM", "PALLADIUM",
}
ENERGY = {
    "CL", "BZ", "BRENT", "WTI", "NATGAS", "NG", "OIL", "USOIL",
    "UKOIL", "WTIOIL", "BRENTOIL",
}


def _ensure_lark_copy() -> None:
    folder = Path(__file__).resolve().parent
    while folder != folder.parent and not (folder / "lark_copy.py").is_file():
        folder = folder.parent
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))


_ensure_lark_copy()
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
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def price_dir() -> Path | None:
    for path in DIRS:
        if path.is_dir():
            return path
    return None


def bucket(base: str, sector: str) -> str | None:
    name = base.strip().upper()
    if name in METALS:
        return "metal"
    if name in ENERGY:
        return "energy"
    if sector in ("Stocks", "Indices"):
        return "stock"
    return None


def load_bins(folder: Path, now_ms: int) -> dict[int, dict[str, float]]:
    days: dict[int, dict[str, float]] = {}
    for path in folder.glob("Binance_*_1d.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        kind = bucket(str(doc.get("base_asset") or ""), str(doc.get("sector") or ""))
        if not kind:
            continue
        for row in doc.get("data") or []:
            if not isinstance(row, dict):
                continue
            ts = row.get("time")
            vol = row.get("volume_usd")
            if not isinstance(ts, (int, float)) or not isinstance(vol, (int, float)):
                continue
            if int(ts) + DAY_MS > now_ms:
                continue
            slot = days.setdefault(int(ts), {"metal": 0.0, "stock": 0.0, "energy": 0.0})
            slot[kind] += float(vol)
    return days


def fmt_usd(n: float) -> str:
    if n >= 1e8:
        return f"{n / 1e8:.2f} 亿美元"
    return f"{n / 1e4:.0f} 万美元"


def fmt_day(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return f"{dt.month}月{dt.day}日"


def build(days: dict[int, dict[str, float]], now_ms: int) -> dict[str, Any] | str:
    if len(days) < 7:
        return "完整日不足 7 天"
    ordered = sorted(days)
    last = ordered[-1]
    if now_ms - (last + DAY_MS) > FRESH_DAYS * DAY_MS:
        return "最新完整日超过 3 天"
    week = ordered[-7:]
    total = 0.0
    for ts in week:
        total += sum(days[ts].values())
    if total <= 0:
        return "近 7 日成交为 0"
    month = datetime.fromtimestamp(last / 1000, tz=timezone.utc).strftime("%Y-%m")
    month_days = [ts for ts in ordered if datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m") == month]
    mix = {"metal": 0.0, "stock": 0.0, "energy": 0.0}
    for ts in month_days:
        for key in mix:
            mix[key] += days[ts][key]
    mix_total = sum(mix.values())
    if mix_total <= 0:
        return "本月分类成交为 0"
    end = datetime.fromtimestamp(last / 1000, tz=timezone.utc)
    iso = end.strftime("%G-W%V")
    return {
        "week_id": iso,
        "start": fmt_day(week[0]),
        "end": fmt_day(last),
        "total": total,
        "metal": mix["metal"] / mix_total * 100,
        "stock": mix["stock"] / mix_total * 100,
        "energy": mix["energy"] / mix_total * 100,
    }


def build_news(view: dict[str, Any]) -> str:
    return (
        "吴说获悉，据吴说数据中心统计，"
        f"{view['start']}至{view['end']}，币安传统金融资产永续成交约 {fmt_usd(view['total'])}。"
        f"本月至今，金属、股票、能源分别约占 {view['metal']:.0f}%、{view['stock']:.0f}% 和 {view['energy']:.0f}%。"
        "股指计入股票。这是币安这三类永续的成交结构，不是全平台合约占比。\n"
        f"{PAGE}"
    )


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def save_state(week_id: str) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(
            {"sentWeek": week_id, "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(
            hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
        ).decode()
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
    args = parser.parse_args()
    folder = price_dir()
    if folder is None:
        print("skip: 没有 tradfi-price 目录")
        return 0
    view = build(load_bins(folder, int(time.time() * 1000)), int(time.time() * 1000))
    if isinstance(view, str):
        print(f"skip: {view}")
        return 0
    text = build_news(view)
    state = load_state()
    if not args.force and state.get("sentWeek") == view["week_id"]:
        print(f"skip: {view['week_id']} 已发")
        print(text)
        return 0
    print(text)
    if not args.push:
        return 0
    env = merge_env()
    webhook = env.get("LARK_ALERT_WEBHOOK") or ""
    if not webhook:
        print("skip: 无 LARK_ALERT_WEBHOOK")
        return 0
    send_lark(webhook, env.get("LARK_ALERT_SECRET"), text)
    save_state(str(view["week_id"]))
    print(f"pushed {view['week_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
