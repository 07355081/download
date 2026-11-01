# -*- coding: utf-8 -*-
"""Aptos 稳定币市值 / 周手续费销毁 → Lark 新闻体。

只发最新：
  - 稳定币：最新完整日创已存序列新高
  - 销毁：最新完整周创近 26 个完整周新高
冷启动建基线不推；不补发上半年旧窗；不含月活。

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
    HERE / "output" / "json" / "aptos.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "aptos" / "aptos.json",
    Path("/root/dashboard/public/json/aptos/aptos.json"),
]
STATE_PATH = HERE / "output" / "aptos_lark.json"
LOG_PATH = HERE / "flash.log"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
TZ_CN = timezone(timedelta(hours=8))
DASHBOARD_URL = "https://data.wublock123.com/dashboard/lark-feeds/aptos"
MAX_AGE_DAYS = 3

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
    raise FileNotFoundError("找不到 aptos.json")


def fmt_usd(n: float) -> str:
    if abs(n) >= 1e8:
        return f"{n / 1e8:.2f} 亿美元"
    if abs(n) >= 1e4:
        return f"{n / 1e4:.0f} 万美元"
    return f"{n:.0f} 美元"


def fmt_apt(n: float) -> str:
    if abs(n) >= 10000:
        return f"{n / 10000:.1f} 万枚"
    return f"{n:.0f} 枚"


def cn_day(day: str) -> str:
    y, m, d = day.split("-")
    return f"{int(y)} 年 {int(m)} 月 {int(d)} 日"


def week_end(monday: str) -> str:
    d = datetime.strptime(monday, "%Y-%m-%d").date() + timedelta(days=6)
    return d.isoformat()


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


def mcap_ath_news(doc: dict[str, Any]) -> tuple[str, str] | None:
    hist = [
        r
        for r in (doc.get("history_mcap") or [])
        if isinstance(r, dict) and isinstance(r.get("market_cap"), (int, float)) and r.get("time")
    ]
    hist.sort(key=lambda r: str(r["time"]))
    if len(hist) < 2:
        return None
    today = datetime.now(timezone.utc).date().isoformat()
    closed = [r for r in hist if str(r["time"]) < today]
    if not closed:
        return None
    row = closed[-1]
    day = str(row["time"])
    age = (datetime.now(timezone.utc).date() - datetime.strptime(day, "%Y-%m-%d").date()).days
    if age > MAX_AGE_DAYS:
        return None
    tvl = float(row["market_cap"])
    earlier = closed[:-1]
    if not earlier or tvl <= max(float(r["market_cap"]) for r in earlier):
        return None
    prior = max(float(r["market_cap"]) for r in earlier)
    msg_id = f"aptos-mcap-ath-{day}"
    text = (
        f"Aptos 稳定币市值创新高\n"
        f"据吴说数据中心统计，截至 {cn_day(day)}，Aptos 链上稳定币流通市值约 {fmt_usd(tvl)}，"
        f"创出已存序列新高（此前高点约 {fmt_usd(prior)}）。"
        "口径为 DefiLlama 分链稳定币，不含 RWA；数字可能与官网略有差异。"
        f"\n{DASHBOARD_URL}"
    )
    return msg_id, text


def burn_half_high_news(doc: dict[str, Any]) -> tuple[str, str] | None:
    fee = doc.get("fee_burn") if isinstance(doc.get("fee_burn"), dict) else {}
    weeks = [r for r in (fee.get("weeks") or []) if isinstance(r, dict) and isinstance(r.get("burn_apt"), (int, float))]
    if len(weeks) < 8:
        return None
    latest = weeks[-1]
    monday = str(latest.get("week") or "")
    if not monday:
        return None
    end = week_end(monday)
    age = (datetime.now(timezone.utc).date() - datetime.strptime(end, "%Y-%m-%d").date()).days
    # 完整周结束后 7 天内可发
    if age < 0 or age > 7:
        return None
    apt = float(latest["burn_apt"])
    window = weeks[-27:-1] if len(weeks) >= 27 else weeks[:-1]
    if not window:
        return None
    prior_peak = max(float(r["burn_apt"]) for r in window)
    if apt <= prior_peak:
        return None
    msg_id = f"aptos-burn-halfyear-{monday}"
    text = (
        f"Aptos 周手续费销毁近半年新高\n"
        f"据吴说数据中心统计，{cn_day(monday)} 至 {cn_day(end)} 这一周，"
        f"Aptos 链上手续费销毁约 {fmt_apt(apt)} APT（约 {fmt_usd(float(latest.get('burn_usd') or 0))}），"
        f"创近 26 个完整周新高（此前高点约 {fmt_apt(prior_peak)} APT）。"
        "口径为 DefiLlama 链手续费（全销毁）按日 APT 价折算；不含月活。"
        f"\n{DASHBOARD_URL}"
    )
    return msg_id, text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    path = resolve_json(args.json or None)
    doc = json.loads(path.read_text(encoding="utf-8"))
    state = load_state()

    briefs: list[tuple[str, str]] = []
    for builder in (mcap_ath_news, burn_half_high_news):
        hit = builder(doc)
        if hit:
            briefs.append(hit)

    print(
        f"[aptos] json={path} mcap={doc.get('stablecoin')} "
        f"latest_week={((doc.get('fee_burn') or {}).get('latest_week'))} hits={len(briefs)}"
    )
    for msg_id, raw in briefs:
        print("---")
        print(f"id={msg_id}\n{as_news(raw)}")

    if not briefs:
        if not state["bootstrapped"]:
            state["bootstrapped"] = True
            save_state(state)
            print("cold-start marked (no hit)")
        else:
            print("skip 无新高")
        return 0

    if not state["bootstrapped"] and not args.force:
        for msg_id, _ in briefs:
            if msg_id not in state["sentIds"]:
                state["sentIds"].append(msg_id)
        state["sentIds"] = state["sentIds"][-80:]
        state["bootstrapped"] = True
        save_state(state)
        print("cold-start: 记基线不推送")
        return 0

    if not args.push:
        print("\n[dry-run]")
        return 0

    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2

    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    for msg_id, raw in briefs:
        if msg_id in state["sentIds"] and not args.force:
            print(f"skip {msg_id}")
            continue
        text = as_news(raw)
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
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"=== {stamp} {msg_id} ok=True\n{text}\n\n")

    state["sentIds"] = state["sentIds"][-80:]
    state["bootstrapped"] = True
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
