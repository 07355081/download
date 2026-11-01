#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SpaceX 官方 IR 最新季度财报 → Lark 新闻体。

只发最新一个 quarter_id；历史不补发。数据来自免费 IR PDF。
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
PAGE = "https://data.wublock123.com/dashboard/tradfi"
ROOT = HERE.parent
JSON_CANDIDATES = [
    Path("/root/dashboard/public/json/spacex-ir/spacex_ir.json"),
    HERE / "output" / "json" / "spacex_ir.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "spacex-ir" / "spacex_ir.json",
]
STATE_PATH = HERE / "output" / "spacex_ir_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
IR_HOME = "https://ir.spacex.com/"


def _ensure_lark_copy() -> None:
    folder = Path(__file__).resolve().parent
    while folder != folder.parent and not (folder / "lark_copy.py").is_file():
        folder = folder.parent
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))


_ensure_lark_copy()
from lark_copy import as_news  # noqa: E402


def env() -> dict[str, str]:
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


def send(webhook: str, secret: str | None, text: str) -> None:
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
        body = json.loads(resp.read().decode() or "{}")
    if str(body.get("code", "0")) != "0":
        raise RuntimeError(body)


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


def resolve_json() -> Path:
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 spacex_ir.json，请先跑 download.py")


def usd_cn(millions: float) -> str:
    """百万美元 → 中文金额（亿/万）。"""
    yi = millions / 100.0
    if abs(yi) >= 1:
        s = f"{yi:.2f}".rstrip("0").rstrip(".")
        return f"{s} 亿"
    wan = millions * 100.0
    return f"{wan:,.0f} 万".replace(",", ",")


def pct_text(v: float | None) -> str:
    if v is None:
        return "—"
    r1 = round(float(v), 1)
    if abs(r1 - round(r1)) < 0.05:
        return f"{int(round(r1))}%"
    return f"{r1:.1f}%"


def build_news(doc: dict[str, Any]) -> tuple[str, str]:
    data = doc["data"]
    qid = str(data["quarter_id"])
    m = data["metrics"]
    rev = m["revenue"]["total"]
    si = m["revenue"]["si"]
    conn = m["revenue"]["connectivity"]
    ebitda = m["adj_ebitda"]["total"]
    capex = m["capex"]["total"]
    si_capex = m["capex"]["si"]
    ni = m.get("net_income_m")
    launches = m.get("launches") or {}
    dig = m.get("digital_assets") or {}

    year, q = int(data["year"]), int(data["quarter"])
    q_label = f"{year}年第{q}季度"

    parts: list[str] = []
    parts.append(
        f"据吴说数据中心统计，SpaceX {q_label} 营收为 {usd_cn(float(rev['current_m']))}美元"
        f"（同比增长 {pct_text(rev.get('yoy_pct'))}，环比增长 {pct_text(rev.get('qoq_pct'))}）。"
    )
    if ni is not None:
        if float(ni) >= 0:
            parts.append(f"净利润为 {usd_cn(float(ni))}美元；")
        else:
            parts.append(f"净亏损为 {usd_cn(abs(float(ni)))}美元；")
    parts.append(
        f"调整后 EBITDA 为 {usd_cn(float(ebitda['current_m']))}美元"
        f"（同比增长 {pct_text(ebitda.get('yoy_pct'))}，环比增长 {pct_text(ebitda.get('qoq_pct'))}）。"
    )
    parts.append(
        f"超级智能（SI）业务营收为 {usd_cn(float(si['current_m']))}美元"
        f"（同比增长 {pct_text(si.get('yoy_pct'))}，环比增长 {pct_text(si.get('qoq_pct'))}）；"
        f"连接业务营收为 {usd_cn(float(conn['current_m']))}美元"
        f"（同比增长 {pct_text(conn.get('yoy_pct'))}，环比增长 {pct_text(conn.get('qoq_pct'))}）。"
    )
    if launches.get("current") is not None:
        cur_l = int(launches["current"])
        pq = launches.get("prior_quarter")
        if pq is not None:
            diff = cur_l - int(pq)
            if diff < 0:
                launch_note = f"较上季度减少 {abs(diff)} 次"
            elif diff > 0:
                launch_note = f"较上季度增加 {diff} 次"
            else:
                launch_note = "与上季度持平"
        else:
            launch_note = ""
        parts.append(f"季度共完成 {cur_l} 次发射" + (f"，{launch_note}" if launch_note else "") + "。")
    parts.append(
        f"季度资本支出为 {usd_cn(float(capex['current_m']))}美元"
        f"（环比增长 {pct_text(capex.get('qoq_pct'))}），"
        f"其中 SI 业务投入 {usd_cn(float(si_capex['current_m']))}美元"
        f"（环比增长 {pct_text(si_capex.get('qoq_pct'))}）。"
    )
    if dig.get("book_m") is not None:
        change = dig.get("change_m")
        if change is not None and change < 0:
            parts.append(
                f"截至季末，数字资产账面价值较去年底减少 {usd_cn(abs(float(change)))}美元"
                f"至 {usd_cn(float(dig['book_m']))}美元。"
            )
        else:
            parts.append(
                f"截至季末，数字资产账面价值为 {usd_cn(float(dig['book_m']))}美元。"
            )
    parts.append("口径来自 SpaceX 官方投资者关系免费财报 PDF，不含卖方一致预期。")
    src = "https://data.wublock123.com/dashboard/tradfi"
    text = "".join(parts) + f"\n{src}"
    msg_id = f"spacex-ir-{qid}"
    return msg_id, text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    args = ap.parse_args()

    path = resolve_json()
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("code") not in (0, "0"):
        print("[spacex-ir-flash] JSON 无效", file=sys.stderr)
        return 1
    msg_id, text = build_news(doc)
    print(msg_id)
    print(text)

    state = load_state()
    if msg_id in state["sentIds"]:
        print(f"[skip] 已发送 {msg_id}")
        return 0
    if not args.push:
        print("[dry-run] 未推送")
        return 0
    conf = env()
    webhook = (conf.get("LARK_ALERT_WEBHOOK") or "").strip()
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 1
    send(webhook, (conf.get("LARK_ALERT_SECRET") or "").strip() or None, text)
    state["sentIds"] = (state.get("sentIds") or []) + [msg_id]
    save_state(state)
    print(f"[pushed] {msg_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
