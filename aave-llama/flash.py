"""Aave DefiLlama 近似月报 → Lark。

- 【实时日常报告】每个上海日最多一条：当前 TVL / 借款 / V4 / Horizon / GHO
- 【历史阈值警报】最近一个已结束月 |环比| ≥ 15%，或 GHO 月末创序列新高
- 不含月活、不含借贷市占。数字不是 Token Terminal。

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
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_PATH = HERE / "output" / "json" / "aave_llama.json"
STATE_PATH = HERE / "output" / "aave_llama_lark.json"
TZ_CN = timezone(timedelta(hours=8))
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/aave-llama"
LARK_MARK_DAILY = "【实时日常报告】"
LARK_MARK_THRESHOLD = "【历史阈值警报】"
MOM_MIN = 15.0
ENV_FILES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]


def _ensure_lark_copy() -> None:
    import sys as _sys
    from pathlib import Path as _Path
    folder = _Path(__file__).resolve().parent
    while folder != folder.parent and not (folder / "lark_copy.py").is_file():
        folder = folder.parent
    if str(folder) not in _sys.path:
        _sys.path.insert(0, str(folder))


_ensure_lark_copy()
from lark_copy import as_news

def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in ENV_FILES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def fmt_usd(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1e8:
        return f"{value / 1e8:.2f} 亿美元"
    if abs(value) >= 1e4:
        return f"{value / 1e4:,.0f} 万美元"
    return f"{value:,.0f} 美元"


def fmt_mom(pct: float | None) -> str:
    if pct is None:
        return "环比暂无"
    if pct >= 0:
        return f"环比增长 {pct:.1f}%"
    return f"环比下降 {abs(pct):.1f}%"


def closed_months(months: list[dict[str, Any]], today: str) -> list[dict[str, Any]]:
    cur = today[:7]
    return [m for m in months if str(m.get("month")) < cur]


def last_closed(series: dict[str, Any], today: str) -> dict[str, Any] | None:
    months = closed_months(series.get("months") or [], today)
    return months[-1] if months else None


def build_body(doc: dict[str, Any], today: str) -> tuple[str, list[str]]:
    by = {s["slug"]: s for s in doc.get("series") or []}
    aave = by.get("aave") or {}
    v4 = by.get("aave-v4") or {}
    horizon = by.get("aave-horizon-rwa") or {}
    gho = doc.get("gho") or {}
    aave_m = last_closed(aave, today)
    v4_m = last_closed(v4, today)
    hz_m = last_closed(horizon, today)
    gho_m = last_closed(gho, today)
    reasons: list[str] = []
    bits = []
    if aave_m:
        bits.append(f"Aave TVL {fmt_mom(aave_m.get('mom_pct'))}，月末约 {fmt_usd(aave_m.get('usd'))}")
        if aave_m.get("mom_pct") is not None and abs(float(aave_m["mom_pct"])) >= MOM_MIN:
            reasons.append(f"Aave TVL {aave_m['month']} {fmt_mom(aave_m.get('mom_pct'))}")
    if aave.get("borrowed_usd") is not None:
        bits.append(f"当前借款余额约 {fmt_usd(aave.get('borrowed_usd'))}")
    if v4_m:
        bits.append(f"Aave V4 {fmt_mom(v4_m.get('mom_pct'))}，月末约 {fmt_usd(v4_m.get('usd'))}")
        if v4_m.get("mom_pct") is not None and abs(float(v4_m["mom_pct"])) >= MOM_MIN:
            reasons.append(f"Aave V4 {v4_m['month']} {fmt_mom(v4_m.get('mom_pct'))}")
    if hz_m:
        bits.append(f"Horizon 月末约 {fmt_usd(hz_m.get('usd'))}（{fmt_mom(hz_m.get('mom_pct'))}）")
        if hz_m.get("mom_pct") is not None and abs(float(hz_m["mom_pct"])) >= MOM_MIN:
            reasons.append(f"Horizon {hz_m['month']} {fmt_mom(hz_m.get('mom_pct'))}")
    if gho_m:
        earlier = [
            float(m["usd"])
            for m in closed_months(gho.get("months") or [], today)
            if m.get("month") != gho_m.get("month") and m.get("usd") is not None
        ]
        ath_txt = ""
        if earlier and gho_m.get("usd") is not None and float(gho_m["usd"]) >= max(earlier):
            ath_txt = "，为序列内新高"
            reasons.append(f"GHO {gho_m['month']} 市值创序列新高")
        bits.append(f"GHO 市值{fmt_mom(gho_m.get('mom_pct'))}，月末约 {fmt_usd(gho_m.get('usd'))}{ath_txt}")
        if gho_m.get("mom_pct") is not None and abs(float(gho_m["mom_pct"])) >= MOM_MIN:
            reasons.append(f"GHO {gho_m['month']} {fmt_mom(gho_m.get('mom_pct'))}")
    month = (aave_m or {}).get("month") or "最近完整月"
    text = (
        f"据吴说数据中心统计（DefiLlama 近似口径，不是 Token Terminal），截至 {today}，"
        f"{month}：{'；'.join(bits)}。"
        "本稿不含月活用户，也不含全市场借贷占比。"
    )
    return f"{text}\n{PAGE}", reasons


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"daily": {}, "threshold": {}}
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"daily": {}, "threshold": {}}
    raw.setdefault("daily", {})
    raw.setdefault("threshold", {})
    return raw


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(datetime.now().timestamp()))
        digest = hmac.new(f"{ts}\n{secret}".encode(), digestmod=hashlib.sha256).digest()
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(digest).decode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if str(body.get("code", "0")) != "0":
        raise RuntimeError(body)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    if not JSON_PATH.is_file():
        print(f"[ERROR] 缺少 {JSON_PATH}", file=sys.stderr)
        return 1
    doc = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    today = datetime.now(TZ_CN).date().isoformat()
    body, reasons = build_body(doc, today)
    state = load_state()
    print(body)
    print("threshold", reasons)
    if not args.push:
        print("[dry-run]", file=sys.stderr)
        return 0
    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 1
    if today not in state["daily"]:
        send_lark(webhook, secret, f"{LARK_MARK_DAILY}\n{body}")
        state["daily"][today] = True
        print("[pushed] daily", file=sys.stderr)
    else:
        print("[skip] daily", file=sys.stderr)
    key = "|".join(reasons)
    if reasons and state["threshold"].get(key) != True:
        tip = "；".join(reasons)
        send_lark(webhook, secret, f"{LARK_MARK_THRESHOLD}\n{tip}。\n{body}")
        state["threshold"][key] = True
        print("[pushed] threshold", file=sys.stderr)
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
