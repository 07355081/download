#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""USA₮（USAT）流通市值 → Lark 新闻体。

读 DefiLlama 稳定币 JSON 的 all_usat 列（非 Anchorage 储备报告）。
只发最新完整日（距今 ≤3 天），不回扫 4 月外电窗：
- 日常：流通市值 + 日环比
- 阈值：创已存序列新高，或 |日环比|≥8%，或较约 30 日前 |变化|≥30%

用法：
  python usat_flash.py
  python usat_flash.py --push
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
STATE_PATH = HERE / "output" / "usat_lark.json"
LOG_PATH = HERE / "usat_flash.log"
PAGE = "https://data.wublock123.com/dashboard/stablecoin-marketcap"
COL = "all_usat"
FRESH_DAYS = 3
DOD_PCT = 8.0
MOM_PCT = 30.0
MOM_LOOKBACK = 30
MARK_DAILY = "【实时日常报告】"
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
    body = as_news(text)
    if text.startswith(MARK_DAILY):
        body = f"{MARK_DAILY}\n{body}"
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": body}}
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
        result = json.loads(resp.read().decode("utf-8") or "{}")
    if str(result.get("code", "0")) != "0":
        raise RuntimeError(result)


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


def load_rows() -> list[dict[str, Any]]:
    for path in MCAP_CANDIDATES:
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        rows = doc.get("data") if isinstance(doc, dict) else doc
        if isinstance(rows, list) and rows:
            print(f"[usat] json={path}")
            return [row for row in rows if isinstance(row, dict)]
    raise FileNotFoundError("找不到 stablecoins_marketcap.json")


def series(rows: list[dict[str, Any]]) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for row in rows:
        day = str(row.get("time") or "")[:10]
        val = row.get(COL)
        if len(day) == 10 and isinstance(val, (int, float)) and float(val) > 0:
            out.append((day, float(val)))
    out.sort()
    return out


def yi(usd: float) -> str:
    if abs(usd) >= 1e8:
        return f"{usd / 1e8:.2f} 亿美元"
    return f"{usd / 1e4:.0f} 万美元"


def cn_day(day: str) -> str:
    y, m, d = day.split("-")
    return f"{int(y)} 年 {int(m)} 月 {int(d)} 日"


def build_text(
    day: str,
    usd: float,
    *,
    dod_pct: float | None,
    mom_pct: float | None,
    reasons: list[str],
    daily: bool,
) -> str:
    dod_bit = f"，较前一日约 {dod_pct:+.1f}%" if dod_pct is not None else ""
    mom_bit = (
        f"较约 {MOM_LOOKBACK} 日前约 {mom_pct:+.1f}%。"
        if mom_pct is not None
        else ""
    )
    lead = (
        f"据吴说数据中心统计，截至 {cn_day(day)}（UTC），USA₮（USAT）流通市值约 {yi(usd)}"
        f"{dod_bit}。{mom_bit}"
    )
    trig = f"触发：{'；'.join(reasons)}。" if reasons else ""
    body = (
        lead
        + trig
        + "口径为 DefiLlama 稳定币流通市值，不是 Anchorage 储备报告或现金/逆回购拆分；"
        "数字可能与外电略有差异。"
        f"\n{PAGE}"
    )
    if daily:
        return f"{MARK_DAILY}\n{body}"
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    try:
        pts = series(load_rows())
    except (OSError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    if len(pts) < 2:
        print("[skip] USAT 序列不足（请先跑 download_defillama）")
        return 0

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    done = [p for p in pts if p[0] < today]
    if len(done) < 2:
        print("[skip] 完整日不足")
        return 0
    (prev_day, prev_usd), (day, usd) = done[-2], done[-1]
    age = (datetime.now(timezone.utc).date() - datetime.strptime(day, "%Y-%m-%d").date()).days
    dod_pct = ((usd / prev_usd) - 1.0) * 100.0 if prev_usd > 0 else None

    target = (
        datetime.strptime(day, "%Y-%m-%d").date() - timedelta(days=MOM_LOOKBACK)
    ).isoformat()
    mom_base = None
    for d, v in reversed(done[:-1]):
        if d <= target:
            mom_base = v
            break
    mom_pct = ((usd / mom_base) - 1.0) * 100.0 if mom_base and mom_base > 0 else None
    hist_peak = max(v for d, v in done if d < day) if len(done) > 1 else prev_usd
    ath = usd > hist_peak

    reasons: list[str] = []
    if ath:
        reasons.append("流通市值创序列新高")
    if dod_pct is not None and abs(dod_pct) >= DOD_PCT:
        reasons.append(f"日环比约 {dod_pct:+.1f}%")
    if mom_pct is not None and abs(mom_pct) >= MOM_PCT:
        reasons.append(f"约 {MOM_LOOKBACK} 日变化约 {mom_pct:+.1f}%")

    print(
        f"[usat] day={day} age={age} usd={usd/1e6:.1f}M prev={prev_usd/1e6:.1f}M "
        f"dod={dod_pct} mom={mom_pct} ath={ath}"
    )

    state = load_state()
    if not state.get("baseline") and not args.force:
        state["baseline"] = True
        save_state(state)
        print("[skip] 首日建基线，不推历史窗")
        return 0
    if age > FRESH_DAYS and not args.force:
        save_state(state)
        print("[skip] 最新完整日过期")
        return 0

    jobs: list[tuple[str, str]] = []
    daily_id = f"usat-daily-{day}"
    jobs.append(
        (
            daily_id,
            build_text(day, usd, dod_pct=dod_pct, mom_pct=mom_pct, reasons=[], daily=True),
        )
    )
    if reasons or args.force:
        tags = hashlib.sha1("|".join(reasons or ["force"]).encode()).hexdigest()[:8]
        jobs.append(
            (
                f"usat-th-{day}-{tags}",
                build_text(
                    day,
                    usd,
                    dod_pct=dod_pct,
                    mom_pct=mom_pct,
                    reasons=reasons or ["强制推送"],
                    daily=False,
                ),
            )
        )

    env = load_env() if args.push else {}
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None

    for msg_id, text in jobs:
        print("---")
        print(msg_id)
        print(as_news(text) if not text.startswith(MARK_DAILY) else text)
        if not args.push:
            continue
        if msg_id in state["sentIds"] and not args.force:
            print(f"[skip] already {msg_id}")
            continue
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
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"=== {stamp} {msg_id} ok=True\n")

    state["sentIds"] = state["sentIds"][-120:]
    save_state(state)
    if not args.push:
        print("\n[dry-run] 未推送")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
