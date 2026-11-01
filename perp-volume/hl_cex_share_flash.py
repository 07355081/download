# -*- coding: utf-8 -*-
"""Hyperliquid 月度永续成交占 CEX 合约样本份额 → Lark 新闻。

数据：已有 cex-asset-vol 分所日合约成交（含 Hyperliquid、Binance）。
口径：占比 = HL / (ALL − HL)；相对币安 = HL / Binance。
只看最近一个已收满自然月；仅当该月占比或相对币安创已存完整月新高才发。
不加警报头，不回扫旧月。月结束后超过 10 个自然日视为过旧不发。
可选附带同月 HIP-3 成交（hip3_by_category）。

用法：
  python hl_cex_share_flash.py
  python hl_cex_share_flash.py --push
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
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CEX_CANDIDATES = [
    ROOT / "cex-asset&vol" / "output" / "json" / "cex_exchange_daily_futures_volume_usd.json",
    Path("/root/data-download/cex-asset&vol/output/json/cex_exchange_daily_futures_volume_usd.json"),
    Path("/root/dashboard/public/json/cex-asset-vol/cex_exchange_daily_futures_volume_usd.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "cex-asset-vol" / "cex_exchange_daily_futures_volume_usd.json",
]
HIP3_CANDIDATES = [
    ROOT / "hyperliquid" / "output" / "json" / "hip3_by_category.json",
    Path("/root/dashboard/public/json/hyperliquid/hip3_by_category.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "hyperliquid" / "hip3_by_category.json",
]
CEX_URL = "https://data.wublock123.com/json/cex-asset-vol/cex_exchange_daily_futures_volume_usd.json"
HIP3_URL = "https://data.wublock123.com/json/hyperliquid/hip3_by_category.json"
STATE_PATH = HERE / "output" / "hl_cex_share_lark.json"
LOG_PATH = HERE / "hl_cex_share_flash.log"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/hyperliquid"
TZ_CN = timezone(timedelta(hours=8))
FRESH_DAYS = 10
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

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
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def gen_lark_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark_text(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
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
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def append_log(stamp: str, msg_id: str, ok: bool, text: str) -> None:
    line = json.dumps({"ts": stamp, "id": msg_id, "ok": ok, "text": text}, ensure_ascii=False)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def fetch_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "dashboard-data/hl-cex-share"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_cex_rows() -> list[dict[str, Any]]:
    for path in CEX_CANDIDATES:
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            break
    else:
        doc = fetch_json(CEX_URL)
    data = doc.get("data") if isinstance(doc, dict) else None
    if isinstance(data, dict) and isinstance(data.get("rows"), list):
        return [r for r in data["rows"] if isinstance(r, dict)]
    if isinstance(doc, dict) and isinstance(doc.get("rows"), list):
        return [r for r in doc["rows"] if isinstance(r, dict)]
    raise RuntimeError("CEX 合约日表结构异常")


def complete_months(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    days: dict[str, set[str]] = defaultdict(set)
    vals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        day = str(row.get("date") or "")
        if len(day) < 10:
            continue
        ym = day[:7]
        days[ym].add(day)
        for ex in ("Hyperliquid", "Binance", "ALL"):
            raw = row.get(ex)
            if raw is None:
                continue
            try:
                vals[ym][ex] += float(raw)
            except (TypeError, ValueError):
                continue
    out: list[dict[str, Any]] = []
    for ym in sorted(vals):
        y, m = int(ym[:4]), int(ym[5:7])
        need = monthrange(y, m)[1]
        if len(days[ym]) < need:
            continue
        hl = float(vals[ym].get("Hyperliquid") or 0)
        bn = float(vals[ym].get("Binance") or 0)
        allv = float(vals[ym].get("ALL") or 0)
        cex = allv - hl
        if hl <= 0 or bn <= 0 or cex <= 0:
            continue
        out.append(
            {
                "month": ym,
                "hl_usd": round(hl, 2),
                "binance_usd": round(bn, 2),
                "cex_ex_hl_usd": round(cex, 2),
                "share_pct": round(hl / cex * 100.0, 4),
                "vs_binance_pct": round(hl / bn * 100.0, 4),
                "month_end": date(y, m, need).isoformat(),
            }
        )
    return out


def hip3_month_usd(ym: str) -> float | None:
    doc: Any = None
    for path in HIP3_CANDIDATES:
        if path.is_file():
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                break
            except (OSError, json.JSONDecodeError):
                continue
    if doc is None:
        try:
            doc = fetch_json(HIP3_URL)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            return None
    data = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(data, list):
        return None
    total = 0.0
    n = 0
    for row in data:
        if not isinstance(row, dict):
            continue
        dt = str(row.get("Date") or "")
        if not dt.startswith(ym):
            continue
        try:
            total += float(row.get("Daily Volume") or 0)
            n += 1
        except (TypeError, ValueError):
            continue
    if n == 0:
        return None
    return total


def yi(usd: float) -> str:
    return f"{usd / 1e8:.2f} 亿美元"


def month_cn(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y} 年 {m} 月"


def build_news(row: dict[str, Any], prior_share: float | None, prior_vs: float | None, hip3: float | None) -> str:
    share = float(row["share_pct"])
    vs = float(row["vs_binance_pct"])
    bits = [
        f"吴说获悉，据吴说数据中心统计，{month_cn(row['month'])} Hyperliquid 永续合约成交约 {yi(float(row['hl_usd']))}，",
        f"占样本内中心化交易所合约成交（不含 Hyperliquid 自身）约 {share:.2f}%",
    ]
    if prior_share is not None and share > prior_share + 1e-9:
        bits.append(f"，创本通道有记录以来新高（此前最高 {prior_share:.2f}%）")
    bits.append(f"；相对于 Binance 约 {vs:.2f}%")
    if prior_vs is not None and vs > prior_vs + 1e-9:
        bits.append(f"，亦创相对币安占比新高（此前最高 {prior_vs:.2f}%）")
    bits.append("。")
    if hip3 is not None and hip3 > 0:
        bits.append(f"同月 HIP-3 成交约 {yi(hip3)}。")
    bits.append(
        "口径为 CoinGecko 经资产校准后的 CEX 合约日成交样本，占比分母不含 Hyperliquid；"
        "与 The Block 等外电样本或有差异。"
        f"\n{DASHBOARD_URL}"
    )
    return as_news("".join(bits))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    try:
        months = complete_months(load_cex_rows())
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] 拉数失败: {exc}", file=sys.stderr)
        return 1
    if not months:
        print("[ERROR] 无完整月", file=sys.stderr)
        return 1

    latest = months[-1]
    prior = months[:-1]
    prior_share = max((float(m["share_pct"]) for m in prior), default=None)
    prior_vs = max((float(m["vs_binance_pct"]) for m in prior), default=None)
    share_ath = prior_share is None or float(latest["share_pct"]) > prior_share + 1e-9
    vs_ath = prior_vs is None or float(latest["vs_binance_pct"]) > prior_vs + 1e-9
    end = date.fromisoformat(latest["month_end"])
    age = (datetime.now(TZ_CN).date() - end).days
    fresh = age <= FRESH_DAYS
    hip3 = hip3_month_usd(latest["month"])

    print(
        f"[hl-cex-share] latest={latest['month']} share={latest['share_pct']:.2f}% "
        f"vsBN={latest['vs_binance_pct']:.2f}% share_ath={share_ath} vs_ath={vs_ath} "
        f"age={age}d fresh={fresh} hip3={None if hip3 is None else round(hip3)}"
    )

    if not (share_ath or vs_ath):
        print("[skip] 最新完整月未创占比或相对币安新高")
        return 0
    if not fresh and not args.force:
        print(f"[skip] 月结束已 {age} 天，超过 {FRESH_DAYS} 天新鲜窗口（不补历史）")
        return 0

    msg_id = f"hl-cex-share-{latest['month']}"
    text = build_news(latest, prior_share, prior_vs, hip3)
    state = load_state()
    print("---")
    print(f"id={msg_id} already={msg_id in state['sentIds']}")
    print(text)
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0

    env = merge_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
        return 0
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    try:
        send_lark_text(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Lark 失败: {exc}", file=sys.stderr)
        append_log(stamp, msg_id, False, text)
        return 1
    print(f"[pushed] {msg_id}")
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-200:]
    save_state(state)
    append_log(stamp, msg_id, True, text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
