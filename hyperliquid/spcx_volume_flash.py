# -*- coding: utf-8 -*-
"""TradeXYZ 的 SPCX 永续日成交创序列新高 → Lark 新闻。

读看板同一份 hip3_by_symbol.json，不新拉接口、不占 Dune。
只用已经结束的 UTC 日，不含当天滚动 24 小时快照。
最新一个完整日高于此前每一天才发一条新闻。更早的高点不补发。
不发每日流水。

用法：
  python spcx_volume_flash.py
  python spcx_volume_flash.py --push
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
STATE_PATH = HERE / "output" / "spcx_volume_lark.json"
LOG_PATH = HERE / "spcx_volume_flash.log"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
JSON_CANDIDATES = [
    Path("/root/dashboard/public/json/hyperliquid/hip3_by_symbol.json"),
    HERE / "output" / "json" / "hip3_by_symbol.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "hyperliquid" / "hip3_by_symbol.json",
]
PAGE = "https://data.wublock123.com/dashboard/hyperliquid"
SYMBOL = "SPCX"
SNAPSHOT_SOURCE = "hyperliquid_info_dayNtlVlm"
MAX_AGE_DAYS = 3
TZ_CN = timezone(timedelta(hours=8))


def _ensure_lark_copy() -> None:
    folder = Path(__file__).resolve().parent
    while folder != folder.parent and not (folder / "lark_copy.py").is_file():
        folder = folder.parent
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))


_ensure_lark_copy()
from lark_copy import as_news  # noqa: E402


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in ENV_CANDIDATES:
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


def find_json() -> Path | None:
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    return None


def completed_volumes(path: Path, today: str) -> dict[str, float]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        return {}
    out: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("Unified Symbol") or row.get("Coin") or "").strip().upper()
        if symbol != SYMBOL:
            continue
        if str(row.get("source") or "") == SNAPSHOT_SOURCE:
            continue
        day = str(row.get("Date") or "")[:10]
        volume = row.get("Daily Volume")
        if not day or day >= today or not isinstance(volume, (int, float)):
            continue
        out[day] = out.get(day, 0.0) + float(volume)
    return out


def yi(value: float) -> str:
    return f"{value / 1e8:.2f} 亿美元"


def build_news(day: str, volume: float, prior: float) -> str:
    return (
        f"吴说获悉，据吴说数据中心统计，{day}（UTC）Hyperliquid HIP-3 提供商 TradeXYZ "
        f"的 SPCX 永续合约成交约 {yi(volume)}，高于本序列此前最高的 {yi(prior)}，创该序列新高。\n"
        f"{PAGE}"
    )


def gen_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark(webhook: str, secret: str | None, text: str) -> None:
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
        body = resp.read().decode("utf-8", errors="replace")
    if body and str(json.loads(body).get("code", "0")) != "0":
        raise RuntimeError(body)


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    path = find_json()
    if path is None:
        print("[skip] 没有 hip3_by_symbol.json")
        return 0
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        volumes = completed_volumes(path, today)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    if len(volumes) < 2:
        print("[skip] SPCX 完整日不足")
        return 0
    latest_day = max(volumes)
    latest = volumes[latest_day]
    age = (datetime.now(timezone.utc).date() - datetime.fromisoformat(latest_day).date()).days
    prior_days = [day for day in volumes if day < latest_day]
    prior = max(volumes[day] for day in prior_days)
    is_ath = latest > prior
    print(
        f"[spcx] file={path.name} latest={latest_day} vol={latest:.0f} prior_high={prior:.0f} "
        f"ath={is_ath} age={age}d",
        flush=True,
    )
    if age > MAX_AGE_DAYS:
        print("[skip] 最新完整日过旧")
        return 0
    if not is_ath and not args.force:
        print("[skip] 未创成交历史新高")
        return 0

    msg_id = f"spcx-volume-ath-{latest_day}"
    text = as_news(build_news(latest_day, latest, prior))
    print("---")
    print(msg_id)
    print(text)
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0

    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    state = load_state()
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
        return 0
    try:
        send_lark(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError, OSError) as exc:
        print(f"[ERROR] Lark 失败: {exc}", file=sys.stderr)
        return 1
    print(f"[pushed] {msg_id}")
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-200:]
    save_state(state)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {datetime.now(TZ_CN).strftime('%Y-%m-%d %H:%M:%S')} {msg_id}\n{text}\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
