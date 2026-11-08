# -*- coding: utf-8 -*-
"""协议近一年收入（不含稳定币发行商和公链）→ 新闻。

只发最新截面一次；之后仅在合计较上次发送变动达到 8%，或采样满 14 日且创本通道新高时再发。
不发每日流水，不回扫，不写同比。
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
    HERE / "output" / "json" / "protocol_revenue_ex.json",
    Path("/root/dashboard/public/json/protocol-revenue-ex/protocol_revenue_ex.json"),
]
STATE_PATH = HERE / "output" / "protocol_revenue_ex_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/protocol-revenue-ex"
MOVE_PCT = 8.0
ATH_MIN_DAYS = 14


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
    raise FileNotFoundError("找不到 protocol_revenue_ex.json")


def yi(n: float) -> str:
    a = abs(n) / 1e8
    if a >= 10:
        return f"{a:.1f} 亿美元"
    return f"{a:.2f} 亿美元"


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
        "lastUsd": doc.get("lastUsd") if isinstance(doc.get("lastUsd"), (int, float)) else None,
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


def build_news(doc: dict[str, Any], latest: dict[str, Any], reason: str) -> str:
    day = str(latest.get("time") or "")[:10]
    usd = float(latest["usd"])
    bits = [f"加密协议近一年收入（不含稳定币发行商和公链）合计约 {yi(usd)}"]
    if reason == "ath":
        bits.append("创本通道记录新高")
    top = doc.get("top") if isinstance(doc.get("top"), list) else []
    parts = []
    for row in top:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        amount = row.get("amount")
        if name and isinstance(amount, (int, float)):
            parts.append(f"{name} 约 {yi(float(amount))}")
    if parts:
        bits.append("其中" + "、".join(parts))
    chg = latest.get("chg_30d_pct")
    d30 = latest.get("usd_30d")
    if isinstance(d30, (int, float)) and isinstance(chg, (int, float)):
        verb = "上升" if chg >= 0 else "下降"
        bits.append(f"近 30 日收入约 {yi(float(d30))}，较前 30 日{verb}约 {abs(chg):.1f}%")
    body = "，".join(bits) + "。"
    return (
        f"吴说获悉，据吴说数据中心统计，截至 {cn_day(day)}，{body}"
        f"口径为 DefiLlama 协议收入按类别加总，已排除稳定币发行商和公链，与第三方研报可能不同。"
        f"\n{PAGE}"
    )


def prior_peak(rows: list[Any], day: str) -> tuple[int, float | None]:
    priors: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_day = str(row.get("time") or "")[:10]
        usd = row.get("usd")
        if row_day and row_day != day and isinstance(usd, (int, float)) and usd > 0:
            priors.append(float(usd))
    if not priors:
        return 0, None
    return len(priors), max(priors)


def should_push(latest: dict[str, Any], rows: list[Any], state: dict[str, Any]) -> tuple[bool, str]:
    day = str(latest.get("time") or "")[:10]
    usd = float(latest["usd"])
    if not state.get("baselined"):
        return True, "latest"
    last = state.get("lastUsd")
    if isinstance(last, (int, float)) and last > 0 and abs(usd / float(last) - 1.0) * 100.0 >= MOVE_PCT:
        return True, "move"
    n_prior, peak = prior_peak(rows, day)
    if n_prior >= ATH_MIN_DAYS - 1 and peak is not None and usd > peak * 1.0005:
        return True, "ath"
    return False, "quiet"


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
    if not isinstance(latest, dict) or not isinstance(latest.get("usd"), (int, float)):
        print("[protocol-revenue-ex] 无最新收入")
        return 0

    day = str(latest.get("time") or "")[:10]
    state = load_state()
    ok, reason = should_push(latest, rows, state)
    print(
        f"[protocol-revenue-ex] json={path} day={day} usd={float(latest['usd']):.0f} "
        f"chg30={latest.get('chg_30d_pct')} reason={reason} baselined={state.get('baselined')}"
    )
    if not ok and not args.force:
        print(f"[skip] {reason}，不发")
        if args.push:
            state["baselined"] = True
            state["lastDay"] = day
            save_state(state)
        return 0

    text = build_news(doc, latest, reason if ok else "latest")
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
    msg_id = f"protocol-revenue-ex-{reason if ok else 'force'}-{day}"
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
        state["baselined"] = True
        save_state(state)
        return 0
    send_lark(webhook, secret, text)
    print("Lark OK", msg_id)
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-60:]
    state["baselined"] = True
    state["lastDay"] = day
    state["lastUsd"] = float(latest["usd"])
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
