# -*- coding: utf-8 -*-
"""苹果永续：近一日成交 + 相对纳斯达克报价的偏离 → Lark 新闻。

只读已有 tradfi-arb-live / tradfi-spot-universe，不新拉行情。
报价超过 2 小时或快照超过 90 分钟则不发。同一美东日只发最新一条，不回扫。

用法：
  python aapl_basis_flash.py
  python aapl_basis_flash.py --push
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
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LIVE_CANDIDATES = [
    HERE / "output" / "json" / "tradfi-arb-live.json",
    Path("/root/dashboard/public/json/tradfi-arb-live.json"),
]
UNI_CANDIDATES = [
    HERE / "output" / "json" / "tradfi-spot-universe.json",
    Path("/root/dashboard/public/json/tradfi-spot-universe.json"),
]
STATE_PATH = HERE / "output" / "aapl_basis_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
PAGE = "https://data.wublock123.com/dashboard/tradfi-spot-arb"
NY = ZoneInfo("America/New_York")
SNAP_MAX_MS = 90 * 60 * 1000
REF_MAX_MS = 2 * 60 * 60 * 1000
# |premium_pct| 超过 1 个百分点视为代码或乘数对不上，不进区间。
MAX_PREM_PCT = 1.0
MIN_LEGS = 4


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


def read_json(paths: list[Path]) -> dict[str, Any]:
    for path in paths:
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("data"), dict):
                return doc["data"]
            if isinstance(doc, dict):
                return doc
    raise FileNotFoundError(paths[0].name)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def save_state(day: str) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps({"sentDay": day, "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, ensure_ascii=False),
        encoding="utf-8",
    )


def fmt_usd(n: float) -> str:
    if n >= 1e8:
        return f"{n / 1e8:.2f} 亿美元"
    return f"{n / 1e4:.0f} 万美元"


def fmt_bp(pct: float) -> str:
    bp = pct * 100
    sign = "+" if bp > 0 else ""
    return f"{sign}{bp:.0f}"


def pick_aapl(live: dict[str, Any], uni: dict[str, Any], now_ms: int) -> dict[str, Any] | str:
    asof = live.get("asof_ms")
    if not isinstance(asof, (int, float)):
        return "快照没有时间"
    asof = int(asof)
    if now_ms - asof > SNAP_MAX_MS:
        return "快照超过 90 分钟"
    row = next((r for r in live.get("rows") or [] if str(r.get("base_asset") or "").upper() == "AAPL"), None)
    if not isinstance(row, dict):
        return "快照里没有 AAPL"
    ref = row.get("ref") if isinstance(row.get("ref"), dict) else {}
    ref_ms = ref.get("asof_ms")
    if row.get("ref_stale") or not isinstance(ref_ms, (int, float)) or now_ms - int(ref_ms) > REF_MAX_MS:
        return "纳斯达克报价已过期"
    if not isinstance(ref.get("last"), (int, float)) or float(ref["last"]) <= 0:
        return "纳斯达克报价无效"
    legs = []
    for leg in row.get("legs") or []:
        if not isinstance(leg, dict):
            continue
        prem = leg.get("premium_pct")
        if not isinstance(prem, (int, float)) or abs(float(prem)) > MAX_PREM_PCT:
            continue
        name = str(leg.get("exchange") or "").strip()
        if name:
            legs.append((name, float(prem)))
    if len(legs) < MIN_LEGS:
        return "可比平台不足"
    asset = next((a for a in uni.get("assets") or [] if str(a.get("base_asset") or "").upper() == "AAPL"), None)
    vol = asset.get("volume_usd_1d") if isinstance(asset, dict) else None
    if not isinstance(vol, (int, float)) or float(vol) <= 0:
        return "没有近一日成交"
    legs.sort(key=lambda item: item[1])
    ny = datetime.fromtimestamp(asof / 1000, tz=NY)
    return {
        "day": ny.strftime("%Y-%m-%d"),
        "when": f"{ny.month}月{ny.day}日{ny.strftime('%H:%M')}",
        "volume": float(vol),
        "lo": legs[0][1],
        "hi": legs[-1][1],
        "lo_name": legs[0][0],
        "hi_name": legs[-1][0],
        "n": len(legs),
    }


def build_news(view: dict[str, Any]) -> str:
    return (
        "吴说获悉，据吴说数据中心统计，"
        f"截至美东时间{view['when']}，加密交易平台上苹果（AAPL）股票永续近一日成交约 {fmt_usd(view['volume'])}。"
        f"相对纳斯达克现货报价，{view['n']} 家可比平台的最新偏离约在 {fmt_bp(view['lo'])} 个基点到 {fmt_bp(view['hi'])} 个基点"
        f"（{view['lo_name']}至{view['hi_name']}）。"
        "成交统计的是加密平台上的股票永续，价格偏离相对纳斯达克现货报价。\n"
        f"{PAGE}"
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
    try:
        live = read_json(LIVE_CANDIDATES)
        uni = read_json(UNI_CANDIDATES)
    except (OSError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"skip: {exc}")
        return 0
    view = pick_aapl(live, uni, int(time.time() * 1000))
    if isinstance(view, str):
        print(f"skip: {view}")
        return 0
    text = build_news(view)
    state = load_state()
    if not args.force and state.get("sentDay") == view["day"]:
        print(f"skip: 美东 {view['day']} 已发")
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
    save_state(view["day"])
    print(f"pushed {view['day']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
