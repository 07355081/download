#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""xStocks Dune 缓存 → Lark 新闻体（只发最新过线，不回扫）。

过线（任一）：
- DEX 累积成交跨过新的 10 亿美元整数关口
- 日成交 |环比| ≥ 50%
- AUM 创本通道新高，或 |日环比| ≥ 10%
- 持有者创本通道新高
- CRCLx 日成交 ≥ 2000 万美元

首日建基线不推；缓存 stale 不推。
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    HERE / "output" / "json" / "xstocks_dune.json",
    Path("/root/dashboard/public/json/xstocks-dune/xstocks_dune.json"),
]
STATE_PATH = HERE / "output" / "xstocks_dune_lark.json"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/xstocks-dune"
SOURCE = "https://dune.com/xstocks/xstocks"
DOD_VOL = 50.0
DOD_AUM = 10.0
CRCLX_MIN = 2e7
CUMUL_STEP = 1e9  # 每跨 10 亿美元关口
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


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def save_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def yi(n: float | None) -> str:
    if n is None:
        return "—"
    if abs(n) >= 1e8:
        return f"{n / 1e8:.2f} 亿美元"
    return f"{n / 1e4:.0f} 万美元"


def load_doc() -> dict[str, Any]:
    for path in JSON_CANDIDATES:
        if path.is_file():
            doc = load_json(path)
            if doc:
                return doc
    raise FileNotFoundError("找不到 xstocks_dune.json")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()

    doc = load_doc()
    state = load_json(STATE_PATH)
    sent = set(state.get("sentIds") or [])
    day = str(doc.get("day") or "")
    if not day:
        print("[skip] no day")
        return 0
    if doc.get("stale"):
        print(f"[skip] stale cache age={doc.get('max_cache_age_hours')}")
        return 0

    aum = doc.get("aum_usd")
    holders = doc.get("holders")
    cumul = doc.get("dex_cumulative_usd")
    daily = doc.get("dex_daily_usd")
    dod = doc.get("dex_daily_dod_pct")
    crclx = doc.get("crclx_daily_usd")

    if not state.get("baseline"):
        state = {
            "baseline": True,
            "sentIds": list(sent),
            "peak_aum": aum,
            "peak_holders": holders,
            "last_cumul_gate": int((cumul or 0) // CUMUL_STEP),
            "prev_aum": aum,
        }
        save_json(STATE_PATH, state)
        print(f"[baseline] day={day} aum={aum} cumul={cumul} holders={holders}")
        return 0

    reasons: list[str] = []
    tags: list[str] = []

    gate = int((cumul or 0) // CUMUL_STEP)
    last_gate = int(state.get("last_cumul_gate") or 0)
    if cumul and gate > last_gate:
        reasons.append(
            f"xStocks DEX 累积成交跨过约 {gate * 10:.0f} 亿美元关口，现约 {yi(float(cumul))}"
        )
        tags.append(f"cumul{gate}")
        state["last_cumul_gate"] = gate

    if isinstance(dod, (int, float)) and abs(float(dod)) >= DOD_VOL:
        reasons.append(f"DEX 日成交较前一日约 {float(dod):+.1f}%")
        tags.append("volDod")

    peak_aum = state.get("peak_aum")
    prev_aum = state.get("prev_aum")
    if isinstance(aum, (int, float)):
        if isinstance(peak_aum, (int, float)) and float(aum) > float(peak_aum) + 1:
            reasons.append(f"AUM 创本通道新高，约 {yi(float(aum))}")
            tags.append("aumAth")
            state["peak_aum"] = aum
        elif isinstance(prev_aum, (int, float)) and float(prev_aum) > 0:
            aum_dod = (float(aum) / float(prev_aum) - 1.0) * 100.0
            if abs(aum_dod) >= DOD_AUM:
                reasons.append(f"AUM 较上次采样约 {aum_dod:+.1f}%")
                tags.append("aumDod")
        state["prev_aum"] = aum
        if not isinstance(peak_aum, (int, float)) or float(aum) > float(peak_aum or 0):
            state["peak_aum"] = aum

    peak_h = state.get("peak_holders")
    if isinstance(holders, int):
        if isinstance(peak_h, int) and holders > peak_h:
            reasons.append(f"持有者创本通道新高，约 {holders:,} 个")
            tags.append("holdersAth")
            state["peak_holders"] = holders
        elif not isinstance(peak_h, int):
            state["peak_holders"] = holders

    if isinstance(crclx, (int, float)) and float(crclx) >= CRCLX_MIN:
        reasons.append(f"CRCLx 日成交约 {yi(float(crclx))}")
        tags.append("crclx")

    if not reasons:
        save_json(STATE_PATH, state)
        print(f"[skip] day={day} no threshold")
        return 0

    msg_id = f"xstocks-dune-{day}-{'-'.join(tags)}"
    if msg_id in sent:
        print(f"[skip] already {msg_id}")
        return 0

    now = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
    line2 = f"{day}：DEX 日成交约 {yi(float(daily) if isinstance(daily, (int, float)) else None)}"
    if isinstance(dod, (int, float)):
        line2 += f"（环比 {float(dod):+.1f}%）"
    line2 += f"，累积约 {yi(float(cumul) if isinstance(cumul, (int, float)) else None)}"
    line2 += f"，AUM 约 {yi(float(aum) if isinstance(aum, (int, float)) else None)}"
    if isinstance(holders, int):
        line2 += f"，持有者约 {holders:,}"
    if isinstance(crclx, (int, float)):
        line2 += f"，CRCLx 日成交约 {yi(float(crclx))}"
    line2 += "。口径为面板 DEX 成交与 Total AUM / Unique Holders，非 CEX+铸造合计；与外电可能略有差异。"
    text = (
        f"吴说获悉，据吴说数据中心统计（Dune xStocks 官方面板缓存），截至 {now}（UTC+8），"
        f"{'；'.join(reasons)}。\n{line2}\n{PAGE}\n源 {SOURCE}"
    )

    print(msg_id)
    print(text)
    if args.push:
        env = load_env()
        webhook = env.get("LARK_ALERT_WEBHOOK")
        if not webhook:
            raise SystemExit("缺少 LARK_ALERT_WEBHOOK")
        send_lark(webhook, env.get("LARK_ALERT_SECRET"), text)
        sent.add(msg_id)
        state["sentIds"] = list(sent)[-200:]
        print(f"[pushed] {msg_id}")
    save_json(STATE_PATH, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
