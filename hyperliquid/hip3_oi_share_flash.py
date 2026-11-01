#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HIP-3 未平仓占 Hyperliquid 总未平仓比例 → Lark 新闻。

实时口径：官方 info metaAndAssetCtxs。主盘（Crypto）+ 各 HIP-3 dex 的
openInterest × markPx。占比 = HIP-3 / (HIP-3 + Crypto)。

只发最新一条新闻稿，不加警报头，不回扫旧日期。同一上海日只发一次。
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
OUT_PATH = HERE / "output" / "json" / "hip3_oi_share.json"
STATE_PATH = HERE / "output" / "hip3_oi_share_lark.json"
LOG_PATH = HERE / "hip3_oi_share_flash.log"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/hyperliquid"
INFO_URL = "https://api.hyperliquid.xyz/info"
TZ_CN = timezone(timedelta(hours=8))
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


def info(body: dict[str, Any]) -> Any:
    data = json.dumps(body).encode("utf-8")
    last: Exception | None = None
    for attempt in range(4):
        req = urllib.request.Request(
            INFO_URL,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "dashboard-data/hip3-oi-share"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in (429, 500, 502, 503):
                raise
            time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"Hyperliquid info 失败: {last}")


def oi_usd(dex: str) -> tuple[float, int]:
    payload = info({"type": "metaAndAssetCtxs", "dex": dex} if dex else {"type": "metaAndAssetCtxs"})
    if not isinstance(payload, list) or len(payload) < 2:
        return 0.0, 0
    meta, ctxs = payload[0], payload[1]
    universe = meta.get("universe") if isinstance(meta, dict) else None
    if not isinstance(universe, list) or not isinstance(ctxs, list):
        return 0.0, 0
    total = 0.0
    for asset, ctx in zip(universe, ctxs):
        if not isinstance(ctx, dict):
            continue
        try:
            total += float(ctx.get("openInterest") or 0) * float(ctx.get("markPx") or 0)
        except (TypeError, ValueError):
            continue
    return total, len(universe)


def snapshot() -> dict[str, Any]:
    dexs = info({"type": "perpDexs"})
    if not isinstance(dexs, list):
        raise RuntimeError("perpDexs 不是列表")
    crypto, n_crypto = oi_usd("")
    hip3 = 0.0
    n_hip3 = 0
    by_dex: list[dict[str, Any]] = []
    for item in dexs:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = str(item["name"])
        oi, n = oi_usd(name)
        hip3 += oi
        n_hip3 += n
        if oi > 0:
            by_dex.append({"dex": name, "oi_usd": round(oi, 2)})
        time.sleep(0.05)
    total = crypto + hip3
    if total <= 0:
        raise RuntimeError("总未平仓为 0")
    share = hip3 / total * 100.0
    now = datetime.now(timezone.utc)
    return {
        "asof": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "day": datetime.now(TZ_CN).strftime("%Y-%m-%d"),
        "crypto_oi_usd": round(crypto, 2),
        "hip3_oi_usd": round(hip3, 2),
        "total_oi_usd": round(total, 2),
        "share_pct": round(share, 2),
        "n_crypto": n_crypto,
        "n_hip3": n_hip3,
        "hip3_dexes": by_dex,
    }


def load_history() -> dict[str, Any]:
    if not OUT_PATH.is_file():
        return {"source": "hyperliquid info metaAndAssetCtxs", "history": []}
    try:
        doc = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"source": "hyperliquid info metaAndAssetCtxs", "history": []}
    hist = doc.get("history") if isinstance(doc, dict) else None
    return {
        "source": "hyperliquid info metaAndAssetCtxs",
        "history": [row for row in hist if isinstance(row, dict)] if isinstance(hist, list) else [],
    }


def save_history(doc: dict[str, Any], row: dict[str, Any]) -> None:
    history = [item for item in doc["history"] if str(item.get("day")) != row["day"]]
    history.append(row)
    history.sort(key=lambda item: str(item.get("day") or ""))
    payload = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "hyperliquid info metaAndAssetCtxs",
        "note": "占比=HIP-3各dex未平仓美元 / (HIP-3+主盘Crypto)。主盘不含 builder 市场。",
        "latest": row,
        "history": history[-400:],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(OUT_PATH)


def yi(usd: float) -> str:
    return f"{usd / 1e8:.2f} 亿美元"


def build_news(row: dict[str, Any], prior_high: float | None) -> str:
    stamp = datetime.now(TZ_CN).strftime("%Y 年 %-m 月 %-d 日 %H:%M").replace(" 0", " ")
    # Windows strftime lacks %-m; use manual
    now = datetime.now(TZ_CN)
    stamp = f"{now.year} 年 {now.month} 月 {now.day} 日 {now.hour:02d}:{now.minute:02d}"
    share = float(row["share_pct"])
    ath = ""
    if prior_high is not None and share > prior_high + 1e-9:
        ath = f"高于本通道已记录的最高点 {prior_high:.2f}%。"
    elif prior_high is not None:
        ath = f"本通道已记录的最高点为 {prior_high:.2f}%。"
    text = (
        f"吴说获悉，据吴说数据中心统计，截至 {stamp}（UTC+8），"
        f"Hyperliquid HIP-3 日度未平仓约 {yi(float(row['hip3_oi_usd']))}，"
        f"主盘 Crypto 约 {yi(float(row['crypto_oi_usd']))}，合计约 {yi(float(row['total_oi_usd']))}。"
        f"HIP-3 占 Hyperliquid 总未平仓约 {share:.2f}%。{ath}"
        f"口径为 Hyperliquid 官方盘口未平仓乘标记价格，HIP-3 为各 builder 市场合计。"
        f"\n{DASHBOARD_URL}"
    )
    return as_news(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    try:
        row = snapshot()
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] 拉数失败: {exc}", file=sys.stderr)
        return 1

    doc = load_history()
    prior = [float(item["share_pct"]) for item in doc["history"] if item.get("day") != row["day"] and isinstance(item.get("share_pct"), (int, float))]
    prior_high = max(prior) if prior else None
    save_history(doc, row)
    print(
        f"[hip3-oi-share] day={row['day']} share={row['share_pct']}% "
        f"hip3={row['hip3_oi_usd']:.0f} crypto={row['crypto_oi_usd']:.0f} prior_high={prior_high}"
    )

    msg_id = f"hip3-oi-share-{row['day']}"
    text = build_news(row, prior_high)
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
