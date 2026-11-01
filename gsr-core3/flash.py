# -*- coding: utf-8 -*-
"""GSR Core3 周权重 → Lark。与看板 cron 同口径；文末挂自有看图页。"""
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
JSON_CANDIDATES = [
    HERE / "output" / "json" / "gsr_core3.json",
    Path("/root/dashboard/public/json/gsr_core3.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "gsr_core3.json",
]
STATE_PATH = HERE / "output" / "gsr_core3_lark.json"
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/gsr-core3"
FRESH_DAYS = 7
TZ_CN = timezone(timedelta(hours=8))
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
ASSETS = (("btc", "比特币"), ("eth", "以太坊"), ("sol", "Solana"))

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
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k.startswith("LARK_") or k == "CRON_SECRET"})
    return env


def load_json() -> dict[str, Any] | None:
    for path in JSON_CANDIDATES:
        if path.is_file():
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(doc, dict):
                return doc
    return None


def fmt(value: float) -> str:
    rounded = round(value * 100) / 100
    if float(rounded).is_integer():
        return f"{rounded:.1f}"
    return str(rounded)


def fmt2(value: float) -> str:
    return f"{round(value * 100) / 100:.2f}"


def signed(value: float) -> str:
    if value > 0:
        return f"+{fmt2(value)}%"
    if value < 0:
        return f"-{fmt2(abs(value))}%"
    return "0.00%"


def weight_move(value: float) -> str:
    abs_v = round(abs(value) * 100) / 100
    if abs_v < 0.05:
        return "持平"
    if value > 0:
        return f"升高 {fmt(abs_v)} 个百分点"
    return f"降低 {fmt(abs_v)} 个百分点"


def day_label(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{y}年{int(m)}月{int(d)}日"


def build_text(doc: dict[str, Any], now: datetime) -> tuple[str, str, bool] | None:
    weeks_raw = doc.get("weeks")
    if not isinstance(weeks_raw, list):
        return None
    rows: list[tuple[str, float, dict[str, float], dict[str, Any]]] = []
    for week in weeks_raw:
        if not isinstance(week, dict):
            continue
        date = week.get("date")
        w = week.get("weights_pct") if isinstance(week.get("weights_pct"), dict) else None
        if not isinstance(date, str) or not w:
            continue
        try:
            y, m, d = map(int, date.split("-"))
            start = datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000
            weights = {k: float(w[k]) for k in ("btc", "eth", "sol")}
        except (TypeError, ValueError, KeyError):
            continue
        rows.append((date, start, weights, week))
    rows.sort(key=lambda r: r[1])
    if not rows:
        return None
    date, start, weights, week = rows[-1]
    prev = rows[-2] if len(rows) >= 2 else None
    historical = (now.timestamp() * 1000 - start) / 86_400_000 > FRESH_DAYS
    label = day_label(date)
    notice = f"{label}这期 Core3 权重距发布已超过 7 天。不是当周新数。" if historical else ""
    body = (
        f"据吴说数据中心统计，{label} Core3 模型组合权重为比特币 {fmt(weights['btc'])}%、"
        f"以太坊 {fmt(weights['eth'])}%、Solana {fmt(weights['sol'])}%。"
    )
    if prev:
        pw = prev[2]
        moves = [f"{name} {weight_move(weights[key] - pw[key])}" for key, name in ASSETS]
        body += f"相对{day_label(prev[0])}的表格，{'，'.join(moves)}。"
    perf = week.get("performance_pct") if isinstance(week.get("performance_pct"), dict) else {}
    ok = True
    bits_1w: list[str] = []
    bits_1m: list[str] = []
    bits_vol: list[str] = []
    for key, name in ASSETS:
        row = perf.get(key) if isinstance(perf, dict) else None
        if not isinstance(row, dict):
            ok = False
            break
        try:
            bits_1w.append(f"{name} {signed(float(row['ret_1w']))}")
            bits_1m.append(f"{name} {signed(float(row['ret_1m']))}")
            bits_vol.append(f"{name} {fmt2(float(row['vol_30d']))}%")
        except (KeyError, TypeError, ValueError):
            ok = False
            break
    if ok:
        body += (
            f"表格中的一周涨跌为{'、'.join(bits_1w)}，"
            f"30 日涨跌为{'、'.join(bits_1m)}，"
            f"30 日波动为{'、'.join(bits_vol)}。"
        )
    text = f"{notice}\n{body}" if historical else body
    text = f"{text}\n{PAGE}"
    return f"gsr-core3-{date}", text, historical


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true", help="忽略已发送状态（慎用）")
    args = parser.parse_args()
    doc = load_json()
    if not doc:
        print("no json", file=sys.stderr)
        return 1
    built = build_text(doc, datetime.now(timezone.utc))
    if not built:
        print("waiting")
        return 0
    send_id, text, historical = built
    mark = "【历史阈值警报】" if historical else "【实时日常报告】"
    full = f"{mark}\n{text}"
    print(as_news(full))
    if not args.push:
        return 0
    state = load_state()
    if not args.force and send_id in state["sentIds"]:
        print("already-sent", send_id)
        return 0
    env = load_env()
    webhook = env.get("LARK_ALERT_WEBHOOK") or env.get("LARK_WEBHOOK") or ""
    if not webhook:
        print("no webhook", file=sys.stderr)
        return 1
    send_lark(webhook, env.get("LARK_ALERT_SECRET") or env.get("LARK_SECRET"), full)
    state["sentIds"] = [*state["sentIds"], send_id][-24:]
    save_state(state)
    print("sent", send_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
