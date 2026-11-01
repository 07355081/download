# -*- coding: utf-8 -*-
"""已上市加密交易所现货周度占比 → 新闻（as_news）。

只发历史级：领先所易主，或四所内 #1–#2 占比差创本通道最窄（需足够周对照）。
不发每周流水；冷启动建基线不发。非 Kaiko。
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
JSON_CANDIDATES = [
    HERE / "output" / "json" / "listed_cex_spot_share.json",
    Path("/root/dashboard/public/json/listed-cex-spot-share/listed_cex_spot_share.json"),
]
STATE_PATH = HERE / "output" / "listed_cex_spot_share_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/listed-cex-spot-share"
GAP_ATH_MIN_WEEKS = 8


def _ensure_lark_copy() -> None:
    folder = Path(__file__).resolve().parent
    while folder != folder.parent and not (folder / "lark_copy.py").is_file():
        folder = folder.parent
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))


_ensure_lark_copy()
from lark_copy import as_news  # noqa: E402


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            out[key] = os.environ[key]
    return out


def resolve_json(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 listed_cex_spot_share.json")


def cn_day(day: str) -> str:
    y, m, d = day.split("-")
    return f"{int(y)} 年 {int(m)} 月 {int(d)} 日"


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": [], "baselined": False}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": [], "baselined": False}
    if not isinstance(doc, dict):
        return {"sentIds": [], "baselined": False}
    ids = doc.get("sentIds")
    return {
        "sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else [],
        "baselined": bool(doc.get("baselined")),
    }


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": as_news(text)}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(
            hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
        ).decode()
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    if raw:
        result = json.loads(raw)
        if str(result.get("code", "0")) != "0":
            raise RuntimeError(result)


def share_line(shares: dict[str, Any]) -> str:
    order = sorted(
        ((str(k), float(v)) for k, v in shares.items() if isinstance(v, (int, float))),
        key=lambda kv: -kv[1],
    )
    return "，".join(f"{name} {pct:.2f}%" for name, pct in order)


def build_news(latest: dict[str, Any], reason: str, prev_leader: str | None) -> str:
    day = str(latest.get("time") or "")[:10]
    shares = latest.get("shares") if isinstance(latest.get("shares"), dict) else {}
    leader = str(latest.get("leader") or "")
    gap = latest.get("gap_pp")
    bits = [
        f"已上市加密交易所（Coinbase、Bullish、Kraken、Gemini）范围内现货周度成交占比约为：{share_line(shares)}"
    ]
    if reason == "leader_swap" and prev_leader and leader and prev_leader != leader:
        bits.append(f"领先所由 {prev_leader} 变为 {leader}")
    if reason == "gap_ath" and isinstance(gap, (int, float)):
        bits.append(f"第一名与第二名占比差约 {gap:.2f} 个百分点，创本通道记录最窄")
    elif isinstance(gap, (int, float)):
        bits.append(f"第一名与第二名占比差约 {gap:.2f} 个百分点")
    body = "；".join(bits) + "。"
    return (
        f"吴说获悉，据吴说数据中心统计，截至 {cn_day(day)} 当周，{body}"
        f"口径为 CoinGecko 交易所 volume_chart 按周加总后在上述四所内重归一化，与 Kaiko 可能不同。"
        f"\n{PAGE}"
    )


def prior_gaps(rows: list[Any], day: str) -> list[float]:
    out: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_day = str(row.get("time") or "")[:10]
        gap = row.get("gap_pp")
        if row_day and row_day != day and isinstance(gap, (int, float)):
            out.append(float(gap))
    return out


def should_push(
    latest: dict[str, Any],
    rows: list[Any],
    baselined: bool,
) -> tuple[bool, str, str | None]:
    day = str(latest.get("time") or "")[:10]
    if not baselined:
        return False, "baseline", None
    # previous week leader
    priors = [r for r in rows if isinstance(r, dict) and str(r.get("time") or "")[:10] < day]
    prev_leader = str(priors[-1].get("leader") or "") if priors else None
    leader = str(latest.get("leader") or "")
    if prev_leader and leader and prev_leader != leader:
        return True, "leader_swap", prev_leader

    gap = latest.get("gap_pp")
    gaps = prior_gaps(rows, day)
    if (
        isinstance(gap, (int, float))
        and len(gaps) >= GAP_ATH_MIN_WEEKS - 1
        and float(gap) < min(gaps) * 0.999
    ):
        return True, "gap_ath", prev_leader
    return False, "quiet", prev_leader


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", default=None)
    args = parser.parse_args()

    path = resolve_json(args.json)
    doc = json.loads(path.read_text(encoding="utf-8"))
    latest = doc.get("latest") if isinstance(doc.get("latest"), dict) else None
    rows = doc.get("data") if isinstance(doc.get("data"), list) else []
    if not latest and rows:
        latest = rows[-1]
    if not isinstance(latest, dict) or not isinstance(latest.get("shares"), dict):
        print("[listed-cex-spot-share] 无最新占比")
        return 0

    day = str(latest.get("time") or "")[:10]
    state = load_state()
    ok, reason, prev_leader = should_push(
        latest, rows if isinstance(rows, list) else [], bool(state.get("baselined"))
    )
    print(
        f"[listed-cex-spot-share] json={path} week={day} leader={latest.get('leader')} "
        f"gap={latest.get('gap_pp')} reason={reason} baselined={state.get('baselined')} "
        f"n={len(rows) if isinstance(rows, list) else 0}"
    )

    if not ok and not args.force:
        print(f"[skip] {reason}，不发")
        if args.push:
            state["baselined"] = True
            state["lastDay"] = day
            save_state(state)
        return 0

    news_reason = reason if ok else "force"
    text = build_news(latest, news_reason, prev_leader)
    print("---")
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

    msg_id = f"listed-cex-spot-share-{news_reason}-{day}"
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
        return 0
    send_lark(webhook, secret, text)
    print("Lark OK", msg_id)
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-60:]
    state["baselined"] = True
    state["lastDay"] = day
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
