# -*- coding: utf-8 -*-
"""X Layer DeFi 锁仓 → Lark 新闻体。

只发最新已闭合日：当日锁仓创出已存序列新高才推。
不回扫 9000 万等历史窗口；活跃地址无免费同源，不写。
冷启动：若最新日已是峰值，只记已发、不推。

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
    HERE / "output" / "json" / "x_layer.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "x-layer" / "x_layer.json",
    Path("/root/dashboard/public/json/x-layer/x_layer.json"),
]
STATE_PATH = HERE / "output" / "x_layer_lark.json"
LOG_PATH = HERE / "flash.log"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
DASHBOARD_URL = "https://data.wublock123.com/dashboard/lark-feeds/x-layer"
MAX_AGE_DAYS = 2

sys.path.insert(0, str(ROOT))
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
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def resolve_json(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 x_layer.json")


def fmt_usd(n: float) -> str:
    if abs(n) >= 1e8:
        return f"{n / 1e8:.2f} 亿美元"
    if abs(n) >= 1e4:
        return f"{n / 1e4:.0f} 万美元"
    return f"{n:.0f} 美元"


def cn_day(day: str) -> str:
    y, m, d = day.split("-")
    return f"{int(y)} 年 {int(m)} 月 {int(d)} 日"


def closed_rows(doc: dict[str, Any], today: str) -> list[dict[str, Any]]:
    rows = [
        r
        for r in (doc.get("history") or [])
        if isinstance(r, dict) and str(r.get("time") or "") < today and isinstance(r.get("tvl"), (int, float))
    ]
    rows.sort(key=lambda r: str(r["time"]))
    return rows


def is_ath(row: dict[str, Any], earlier: list[dict[str, Any]]) -> bool:
    tvl = float(row["tvl"])
    if not earlier:
        return False
    prior = max(float(r["tvl"]) for r in earlier)
    return tvl > prior


def build_news(doc: dict[str, Any], row: dict[str, Any], earlier: list[dict[str, Any]]) -> str:
    day = str(row["time"])
    tvl = float(row["tvl"])
    prior_peak = max(float(r["tvl"]) for r in earlier) if earlier else None
    aave = row.get("aave_tvl")
    uni = row.get("uniswap_tvl")
    body = (
        f"据吴说数据中心统计，截至 {cn_day(day)}，"
        f"OKX 旗下 X Layer 网络 DeFi 锁仓约 {fmt_usd(tvl)}"
    )
    if prior_peak is not None:
        body += f"，创出已存序列新高（此前高点约 {fmt_usd(prior_peak)}）"
    body += "。"
    bits: list[str] = []
    if isinstance(aave, (int, float)):
        share = (float(aave) / tvl * 100.0) if tvl > 0 else None
        bits.append(
            f"Aave V3 约 {fmt_usd(float(aave))}"
            + (f"（约占 {share:.1f}%）" if share is not None else "")
        )
    if isinstance(uni, (int, float)):
        share = (float(uni) / tvl * 100.0) if tvl > 0 else None
        bits.append(
            f"Uniswap 约 {fmt_usd(float(uni))}"
            + (f"（约占 {share:.1f}%）" if share is not None else "")
        )
    if bits:
        body += "其中" + "，".join(bits) + "。"
    # 相对 4 月末（完整月末）
    apr = next((r for r in reversed(earlier) if str(r["time"]).startswith("2026-04-30")), None)
    if apr and float(apr["tvl"]) > 0:
        multiple = tvl / float(apr["tvl"])
        body += f"相较 2026 年 4 月末约 {fmt_usd(float(apr['tvl']))}，约为 {multiple:.1f} 倍。"
    body += (
        "口径为 DefiLlama 链上 DeFi 锁仓与协议分链，非 Dune 官方面板；不含活跃地址。"
        f"\n{DASHBOARD_URL}"
    )
    return f"X Layer DeFi 锁仓创新高\n{body}"


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(time.time()))
        digest = hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(digest).decode("utf-8")
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
            raise RuntimeError(result)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": [], "bootstrapped": False}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": [], "bootstrapped": False}
    ids = doc.get("sentIds") if isinstance(doc, dict) else []
    return {
        "sentIds": [str(x) for x in ids] if isinstance(ids, list) else [],
        "bootstrapped": bool(doc.get("bootstrapped")) if isinstance(doc, dict) else False,
    }


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

    path = resolve_json(args.json or None)
    doc = json.loads(path.read_text(encoding="utf-8"))
    today = datetime.now(timezone.utc).date().isoformat()
    rows = closed_rows(doc, today)
    if not rows:
        print("[ERROR] 无已闭合日", file=sys.stderr)
        return 1
    row = rows[-1]
    day = str(row["time"])
    age = (datetime.now(timezone.utc).date() - datetime.strptime(day, "%Y-%m-%d").date()).days
    earlier = rows[:-1]
    ath = is_ath(row, earlier)
    msg_id = f"x-layer-ath-{day}"
    print(f"[x-layer] json={path} closed={day} age={age}d ath={ath} tvl={row.get('tvl')}")

    if age > MAX_AGE_DAYS:
        print("[ERROR] 数据过旧，不发", file=sys.stderr)
        return 1

    state = load_state()
    if not ath:
        print("skip 非新高")
        if not state["bootstrapped"]:
            state["bootstrapped"] = True
            save_state(state)
            print("cold-start marked")
        return 0

    raw = build_news(doc, row, earlier)
    text = as_news(raw)
    print(f"id={msg_id}\n{text}")

    if not state["bootstrapped"] and not args.force:
        # 冷启动：最新日已是峰值时不推这篇历史高点新闻
        state["bootstrapped"] = True
        if msg_id not in state["sentIds"]:
            state["sentIds"].append(msg_id)
        state["sentIds"] = state["sentIds"][-60:]
        save_state(state)
        print("cold-start: 记基线不推送")
        return 0

    if not args.push:
        print("\n[dry-run]")
        return 0

    if msg_id in state["sentIds"] and not args.force:
        print(f"skip {msg_id}")
        return 0

    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2

    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    try:
        send_lark(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
        print(f"[ERROR] Lark: {exc}", file=sys.stderr)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"=== {stamp} {msg_id} ok=False {exc}\n{text}\n\n")
        return 1

    print(f"  → Lark OK {msg_id}")
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-60:]
    state["bootstrapped"] = True
    save_state(state)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"=== {stamp} {msg_id} ok=True\n{text}\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
