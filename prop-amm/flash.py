#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prop AMM 占 DEX 成交 → Lark 新闻体。

Solana：HumidiFi、BisonFi、Tessera、Manifest 等协议 7 日成交 / Solana DEX。
Ethereum：FermiSwap、Kipseli、bopAMM 7 日成交 / 以太坊 DEX。
数据是 DefiLlama 免费 overview/summary，不是 Entropy 专表，也不是 ETH-USD 单交易对。
只在最新对齐日的 7 日占比创本序列新高、且该日不超过 3 天时发一条。不发日常，不回扫旧高点。
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
OUT_PATH = HERE / "output" / "json" / "prop_amm.json"
STATE_PATH = HERE / "output" / "prop_amm_lark.json"
LOG_PATH = HERE / "flash.log"
JSON_URL = "https://data.wublock123.com/dashboard/dex-fees"
PAGE = "https://data.wublock123.com/dashboard/dex-fees"
API = "https://api.llama.fi"
SOL_SLUGS = (
    "bisonfi", "manifest-trade", "tessera-v", "goonfi", "scorch", "solfi-v2",
    "alphaq", "zerofi", "quantumamm", "aquifer", "humidifi",
)
ETH_SLUGS = ("fermiswap", "kipseli", "bopamm")
TZ_CN = timezone(timedelta(hours=8))
FRESH_DAYS = 3
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


def gen_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": as_news(text)}}
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
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def http_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "dashboard-data/prop-amm", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def chart_map(url: str) -> dict[str, float]:
    doc = http_json(url)
    out: dict[str, float] = {}
    for item in doc.get("totalDataChart") or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        day = datetime.fromtimestamp(int(item[0]), timezone.utc).strftime("%Y-%m-%d")
        out[day] = float(item[1] or 0)
    if len(out) < 30:
        raise RuntimeError(f"序列太短: {url}")
    return out


def chain_url(chain: str) -> str:
    return f"{API}/overview/dexs/{chain}?excludeTotalDataChart=false&excludeTotalDataChartBreakdown=true"


def proto_url(slug: str) -> str:
    return f"{API}/summary/dexs/{slug}?excludeTotalDataChart=false&excludeTotalDataChartBreakdown=true"


def end_day(parts: list[dict[str, float]]) -> str:
    counts: dict[str, int] = {}
    for part in parts:
        if not part:
            continue
        last = max(part)
        counts[last] = counts.get(last, 0) + 1
    need = max(3, len(parts) // 2)
    days = [day for day, count in counts.items() if count >= need]
    if not days:
        raise RuntimeError("协议日期对不齐")
    return max(days)


def rolling(total: dict[str, float], parts: list[dict[str, float]], stop: str) -> list[dict[str, Any]]:
    days = [day for day in sorted(total) if day <= stop]
    rows = []
    for i, day in enumerate(days):
        if i < 6:
            continue
        window = days[i - 6 : i + 1]
        den = sum(total[item] for item in window)
        num = sum(part.get(item, 0.0) for part in parts for item in window)
        if den <= 0:
            continue
        rows.append({
            "day": day,
            "share_pct": round(num / den * 100, 2),
            "volume_usd": round(num, 2),
            "dex_usd": round(den, 2),
        })
    return rows


def yi(usd: float) -> str:
    return f"{usd / 1e8:.0f} 亿美元"


def build_news(sol: dict[str, Any] | None, eth: dict[str, Any] | None, sol_high: bool, eth_high: bool) -> str:
    bits = ["据吴说数据中心统计，"]
    if sol and sol_high:
        bits.append(
            f"截至 {sol['day']} 的过去 7 日，Solana 上 HumidiFi、BisonFi、Tessera、Manifest 等协议合计成交约 {yi(sol['volume_usd'])}，"
            f"占 Solana DEX 约 {sol['share_pct']:.1f}%，为本序列新高。"
        )
    if eth and eth_high:
        bits.append(
            f"截至 {eth['day']} 的过去 7 日，FermiSwap、Kipseli、bopAMM 合计成交约 {yi(eth['volume_usd'])}，"
            f"占以太坊 DEX 约 {eth['share_pct']:.1f}%，为本序列新高。"
        )
    return "".join(bits) + f"\n{PAGE}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        sol_total = chart_map(chain_url("solana"))
        sol_parts = [chart_map(proto_url(slug)) for slug in SOL_SLUGS]
        eth_total = chart_map(chain_url("ethereum"))
        eth_parts = [chart_map(proto_url(slug)) for slug in ETH_SLUGS]
        sol_stop = end_day(sol_parts)
        eth_stop = end_day(eth_parts)
        sol_rows = rolling(sol_total, sol_parts, sol_stop)
        eth_rows = rolling(eth_total, eth_parts, eth_stop)
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError, ValueError, KeyError) as exc:
        print(f"[ERROR] 拉数失败: {exc}", file=sys.stderr)
        return 1
    if len(sol_rows) < 30 or len(eth_rows) < 30:
        print("[ERROR] 7 日占比序列太短", file=sys.stderr)
        return 1
    sol_latest, eth_latest = sol_rows[-1], eth_rows[-1]
    sol_high = sol_latest["share_pct"] > max(row["share_pct"] for row in sol_rows[:-1]) + 0.05
    eth_high = eth_latest["share_pct"] > max(row["share_pct"] for row in eth_rows[:-1]) + 0.05
    today = datetime.now(timezone.utc).date()
    sol_age = (today - datetime.strptime(sol_latest["day"], "%Y-%m-%d").date()).days
    eth_age = (today - datetime.strptime(eth_latest["day"], "%Y-%m-%d").date()).days
    payload = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "DefiLlama overview/dexs + summary/dexs",
        "note": "Solana 名单为 11 个协议，不是 Entropy 全量 PropAMM。以太坊占比的分母是整链 DEX，不是 ETH-USD 交易对。",
        "solana": {"slugs": list(SOL_SLUGS), "latest": sol_latest, "new_high": sol_high},
        "ethereum": {"slugs": list(ETH_SLUGS), "latest": eth_latest, "new_high": eth_high},
        "solana_tail": sol_rows[-90:],
        "ethereum_tail": eth_rows[-90:],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(
        f"[prop-amm] sol {sol_latest['day']} {sol_latest['share_pct']}% high={sol_high} age={sol_age}d | "
        f"eth {eth_latest['day']} {eth_latest['share_pct']}% high={eth_high} age={eth_age}d"
    )
    fresh_sol = sol_high and sol_age <= FRESH_DAYS
    fresh_eth = eth_high and eth_age <= FRESH_DAYS
    if not fresh_sol and not fresh_eth and not args.force:
        print("[skip] 7 日占比未创新高，或最新日已超过 3 天")
        return 0
    text = build_news(sol_latest, eth_latest, fresh_sol or args.force and sol_high, fresh_eth or args.force and eth_high)
    if not text.startswith("据吴说"):
        print("[skip] 没有可写的新高")
        return 0
    day = sol_latest["day"] if fresh_sol else eth_latest["day"]
    msg_id = f"prop-amm-{day}-sol{int(fresh_sol)}-eth{int(fresh_eth)}"
    print("---")
    print(f"id={msg_id}")
    print(as_news(text))
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
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    try:
        send_lark(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Lark 失败: {exc}", file=sys.stderr)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"=== {stamp} {msg_id} ok=False {exc}\n{text}\n\n")
        return 1
    print(f"[pushed] {msg_id}")
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-200:]
    save_state(state)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} {msg_id} ok=True\n{text}\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
