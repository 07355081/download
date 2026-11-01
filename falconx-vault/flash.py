# -*- coding: utf-8 -*-
"""FalconX Credit Vault TVL → Lark。

只发最新采样日，不回扫历史。
- 日常：【实时日常报告】+ 可发稿新闻
- 阈值：自有序列创 TVL 新高，或日环比 |Δ|≥15% →【历史阈值警报】（新高加【特殊警报】）

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    HERE / "output" / "json" / "falconx_vault.json",
    Path("/root/dashboard/public/json/falconx-vault/falconx_vault.json"),
]
STATE_PATH = HERE / "output" / "falconx_vault_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/falconx-vault"
DOD_PCT = 15.0
LARK_DAILY = "【实时日常报告】"
LARK_THRESHOLD = "【历史阈值警报】"
LARK_SPECIAL = "【特殊警报】"


def _ensure_lark_copy() -> None:
    folder = Path(__file__).resolve().parent
    while folder != folder.parent and not (folder / "lark_copy.py").is_file():
        folder = folder.parent
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))


_ensure_lark_copy()
from lark_copy import as_news


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
    raise FileNotFoundError("找不到 falconx_vault.json")


def fmt_yi(n: float) -> str:
    a = abs(n) / 1e8
    if a >= 10:
        return f"{a:.1f} 亿美元"
    return f"{a:.2f} 亿美元"


def fmt_pct(n: float) -> str:
    sign = "+" if n > 0 else ""
    return f"{sign}{n:.1f}%"


def cn_day(day: str) -> str:
    y, m, d = day.split("-")
    return f"{int(y)} 年 {int(m)} 月 {int(d)} 日"


def build_news(doc: dict[str, Any], latest: dict[str, Any]) -> str:
    day = str(latest["time"])
    tvl = float(latest["tvl_usd"])
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    bits = [f"Pareto Credit 旗下 FalconX Credit Vault 锁仓约 {fmt_yi(tvl)}"]
    peak = latest.get("peak_tvl_usd")
    vs = latest.get("vs_peak_pct")
    if isinstance(peak, (int, float)) and isinstance(vs, (int, float)):
        if abs(float(vs)) < 0.05:
            bits.append("创本序列锁仓新高")
        else:
            bits.append(f"较本序列峰值 {fmt_pct(float(vs))}（峰值约 {fmt_yi(float(peak))}）")
    ytd = latest.get("ytd_pct")
    ytd_day = latest.get("ytd_base_day")
    if isinstance(ytd, (int, float)) and ytd_day:
        bits.append(f"相对序列内 {cn_day(str(ytd_day))} 约 {fmt_pct(float(ytd))}")
    dod = latest.get("dod_pct")
    if isinstance(dod, (int, float)):
        bits.append(f"较上一采样日 {fmt_pct(float(dod))}")
    body = "，".join(bits) + "。"
    return (
        f"据吴说数据中心统计，截至 {cn_day(day)}，{body}"
        f"口径为 DefiLlama Yields（{meta.get('project')}/{meta.get('pool_meta')}），非 Token Terminal。"
        f"\n\n{PAGE}"
    )


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
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    body = as_news(text)
    heads: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s in (LARK_THRESHOLD, LARK_SPECIAL, LARK_DAILY):
            heads.append(s)
        elif not s:
            continue
        else:
            break
    if heads:
        body = "\n".join(heads) + "\n" + body
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": body}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(
            hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
        ).decode()
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    if raw:
        result = json.loads(raw)
        if str(result.get("code", "0")) != "0":
            raise RuntimeError(result)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", default=None)
    args = parser.parse_args()

    doc = json.loads(resolve_json(args.json).read_text(encoding="utf-8"))
    latest = doc.get("latest") if isinstance(doc.get("latest"), dict) else None
    rows = doc.get("data") if isinstance(doc.get("data"), list) else []
    if not latest and rows:
        latest = rows[-1]
    if not isinstance(latest, dict) or not isinstance(latest.get("tvl_usd"), (int, float)):
        print("[falconx-vault] 无最新 TVL")
        return 0

    day = str(latest.get("time") or doc.get("meta", {}).get("as_of") or "")[:10]
    news = build_news(doc, latest)
    briefs: list[tuple[str, str]] = [
        (f"falconx-vault-daily-{day}", f"{LARK_DAILY}\n{news}"),
    ]

    vs = latest.get("vs_peak_pct")
    dod = latest.get("dod_pct")
    is_ath = isinstance(vs, (int, float)) and abs(float(vs)) < 0.05
    big_move = isinstance(dod, (int, float)) and abs(float(dod)) >= DOD_PCT
    if is_ath or big_move:
        mark = f"{LARK_THRESHOLD}\n{LARK_SPECIAL}" if is_ath else LARK_THRESHOLD
        briefs.append((f"falconx-vault-threshold-{day}", f"{mark}\n{news}"))

    state = load_state()
    for msg_id, text in briefs:
        print("---", msg_id, "already" if msg_id in state["sentIds"] else "new")
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

    for msg_id, text in briefs:
        if msg_id in state["sentIds"] and not args.force:
            print(f"[skip] {msg_id}")
            continue
        try:
            send_lark(webhook, secret, text)
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] {msg_id}: {exc}", file=sys.stderr)
            return 1
        if msg_id not in state["sentIds"]:
            state["sentIds"].append(msg_id)
        print(f"[pushed] {msg_id}")
        time.sleep(0.3)
    state["sentIds"] = state["sentIds"][-120:]
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
