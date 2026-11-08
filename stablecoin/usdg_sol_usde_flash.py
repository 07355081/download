#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""USDG 总市值 / Solana USDe → Lark 新闻体。

读已有 DefiLlama 稳定币 JSON，不新拉数。
只看最新完整日（距今 ≤2 天），不回扫：
- USDG 从前一日下方新穿 30 亿美元
- 或 Solana USDe 日倍数 ≥10 且增量 ≥2000 万美元

首日只建基线。外电「350 万→7000 万」与本序列量级不同，不以该笔补发。

用法：
  python usdg_sol_usde_flash.py
  python usdg_sol_usde_flash.py --push
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
MCAP_CANDIDATES = [
    HERE / "output" / "json" / "stablecoins_marketcap.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "stablecoin" / "stablecoins_marketcap.json",
    Path("/root/dashboard/public/json/stablecoin/stablecoins_marketcap.json"),
]
BREAK_CANDIDATES = [
    HERE / "output" / "json" / "stablecoins_marketcap_breakdown.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "stablecoin" / "stablecoins_marketcap_breakdown.json",
    Path("/root/dashboard/public/json/stablecoin/stablecoins_marketcap_breakdown.json"),
]
STATE_PATH = HERE / "output" / "usdg_sol_usde_lark.json"
LOG_PATH = HERE / "usdg_sol_usde_flash.log"
PAGE = "https://data.wublock123.com/dashboard/stablecoin-marketcap"
USDG_LINE = 3_000_000_000.0
USDE_MULT = 10.0
USDE_DELTA = 20_000_000.0
FRESH_DAYS = 2
TZ_CN = timezone(timedelta(hours=8))
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

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
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": as_news(text)}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(
            hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
        ).decode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode("utf-8") or "{}")
    if str(body.get("code", "0")) != "0":
        raise RuntimeError(body)


def load_state() -> dict[str, Any]:
    empty = {"sentIds": [], "baseline": False}
    if not STATE_PATH.is_file():
        return dict(empty)
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(empty)
    ids = doc.get("sentIds") if isinstance(doc, dict) else None
    return {
        "sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else [],
        "baseline": bool(doc.get("baseline")) if isinstance(doc, dict) else False,
    }


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def load_rows(candidates: list[Path]) -> list[dict[str, Any]]:
    for path in candidates:
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        rows = doc.get("data") if isinstance(doc, dict) else doc
        if isinstance(rows, list) and rows:
            print(f"[usdg-sol] json={path}")
            return [row for row in rows if isinstance(row, dict)]
    raise FileNotFoundError("找不到稳定币 JSON")


def series(rows: list[dict[str, Any]], col: str) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for row in rows:
        day = str(row.get("time") or "")[:10]
        val = row.get(col)
        if len(day) == 10 and isinstance(val, (int, float)):
            out.append((day, float(val)))
    out.sort()
    return out


def complete_pair(points: list[tuple[str, float]]) -> tuple[tuple[str, float], tuple[str, float]] | None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    done = [p for p in points if p[0] < today]
    if len(done) < 2:
        return None
    return done[-2], done[-1]


def yi(usd: float) -> str:
    if abs(usd) >= 1e8:
        return f"{usd / 1e8:.2f} 亿美元"
    return f"{usd / 1e4:.0f} 万美元"


def build_news(
    day: str,
    *,
    cross: bool,
    jump: bool,
    usdg: float,
    usdg_prev: float,
    sol_usdg: float | None,
    sol_usde: float | None,
    sol_usde_prev: float | None,
) -> str:
    y, m, d = day.split("-")
    day_cn = f"{y} 年 {int(m)} 月 {int(d)} 日"
    bits: list[str] = []
    if cross:
        sol_bit = f"其中 Solana 上 USDG 约 {yi(sol_usdg)}。" if sol_usdg is not None else ""
        bits.append(
            f"{day_cn}（UTC）USDG 流通市值约 {yi(usdg)}，"
            f"前一日约 {yi(usdg_prev)}，新穿 30 亿美元。{sol_bit}"
        )
    if jump and sol_usde is not None and sol_usde_prev and sol_usde_prev > 0:
        mult = sol_usde / sol_usde_prev
        bits.append(
            f"同日 Solana 网络 USDe 流通约 {yi(sol_usde)}，"
            f"前一日约 {yi(sol_usde_prev)}，约 {mult:.1f} 倍。"
        )
    return (
        "吴说获悉，据吴说数据中心统计，"
        + "".join(bits)
        + "口径为 DefiLlama 稳定币流通市值，不是单笔部署或 Ethena 公告；数字可能与外电略有差异。"
        + f"\n{PAGE}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        mcap = load_rows(MCAP_CANDIDATES)
        brk = load_rows(BREAK_CANDIDATES)
    except (OSError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"[ERROR] 读数失败: {exc}", file=sys.stderr)
        return 1

    usdg_pts = series(mcap, "all_usdg")
    usde_pts = series(brk, "solana_usde")
    solg_pts = series(brk, "solana_usdg")
    pair = complete_pair(usdg_pts)
    if pair is None:
        print("[skip] USDG 完整日不足")
        return 0
    (prev_day, usdg_prev), (day, usdg) = pair
    age = (
        datetime.now(timezone.utc).date() - datetime.strptime(day, "%Y-%m-%d").date()
    ).days
    usde_map = dict(usde_pts)
    solg_map = dict(solg_pts)
    sol_usde = usde_map.get(day)
    sol_usde_prev = usde_map.get(prev_day)
    sol_usdg = solg_map.get(day)
    cross = usdg_prev < USDG_LINE <= usdg
    jump = (
        sol_usde is not None
        and sol_usde_prev is not None
        and sol_usde_prev > 0
        and sol_usde / sol_usde_prev >= USDE_MULT
        and sol_usde - sol_usde_prev >= USDE_DELTA
    )
    print(
        f"[usdg-sol] day={day} age={age} usdg={usdg/1e9:.2f}B prev={usdg_prev/1e9:.2f}B "
        f"sol_usde={None if sol_usde is None else round(sol_usde/1e6,1)} "
        f"cross={cross} jump={jump}"
    )

    state = load_state()
    if not state.get("baseline") and not args.force:
        state["baseline"] = True
        save_state(state)
        print("[skip] 首日建基线")
        return 0

    if age > FRESH_DAYS and not args.force:
        save_state(state)
        print("[skip] 最新完整日过期")
        return 0
    if not cross and not jump and not args.force:
        save_state(state)
        print("[skip] 未过线")
        return 0

    tags = []
    if cross:
        tags.append("usdg3b")
    if jump:
        tags.append("solusde")
    if not tags:
        tags.append("force")
    msg_id = f"usdg-sol-{day}-{'-'.join(tags)}"
    text = build_news(
        day,
        cross=cross or args.force,
        jump=jump,
        usdg=usdg,
        usdg_prev=usdg_prev,
        sol_usdg=sol_usdg,
        sol_usde=sol_usde,
        sol_usde_prev=sol_usde_prev,
    )
    print("---")
    print(msg_id)
    print(as_news(text))
    if not args.push:
        save_state(state)
        print("\n[dry-run] 未推送")
        return 0
    if msg_id in state["sentIds"] and not args.force:
        save_state(state)
        print(f"[skip] already {msg_id}")
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
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Lark 失败: {exc}", file=sys.stderr)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"=== {stamp} {msg_id} ok=False {exc}\n")
        return 1
    print(f"[pushed] {msg_id}")
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-80:]
    save_state(state)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} {msg_id} ok=True\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
