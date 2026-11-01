"""Binance 在 Tron 上的 USDT → Lark 新闻体。

只看最新日。单日下降达到 15%，或两日下降达到 25%，才发。
不发每日例行，不回扫，不用警报抬头。
口径是 DefiLlama 标注地址合计，不是 CryptoQuant。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    HERE / "output" / "json" / "binance_tron_usdt.json",
    Path("/root/dashboard/public/json/binance-tron-usdt/binance_tron_usdt.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "binance-tron-usdt" / "binance_tron_usdt.json",
]
STATE_PATH = HERE / "output" / "binance_tron_usdt_lark.json"
PAGE = "https://data.wublock123.com/dashboard/binance-tron-usdt"
CHART = "https://data.wublock123.com/dashboard/binance-incident"
DROP_1D = -15.0
DROP_2D = -25.0
FRESH_DAYS = 3
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
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def fmt_usd(value: float) -> str:
    if abs(value) >= 1e8:
        return f"{value / 1e8:.2f} 亿美元"
    return f"{value / 1e4:,.0f} 万美元"


def fmt_pct(value: float) -> str:
    return f"{'+' if value > 0 else ''}{value:.1f}%"


def cn_md(day: str) -> str:
    _year, month, date = day.split("-")
    return f"{int(month)}月{int(date)}日"


def load_series() -> list[dict[str, Any]]:
    for path in JSON_CANDIDATES:
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        series = [
            row
            for row in (doc.get("series") or [])
            if isinstance(row, dict) and row.get("day") and isinstance(row.get("usdt_usd"), (int, float))
        ]
        if len(series) >= 3:
            series.sort(key=lambda row: str(row["day"]))
            print(f"[binance-tron-usdt] json={path} n={len(series)}")
            return series
    raise FileNotFoundError("找不到 binance_tron_usdt.json")


def build_news(latest: dict[str, Any], prev: dict[str, Any], base: dict[str, Any], avg: float, why: str) -> str:
    day = str(latest["day"])
    total = float(latest["usdt_usd"])
    prev_total = float(prev["usdt_usd"])
    base_total = float(base["usdt_usd"])
    delta = total - prev_total
    two_delta = total - base_total
    two = two_delta / base_total * 100.0
    dod = delta / prev_total * 100.0
    word = "多" if delta >= 0 else "少"
    two_word = "多" if two_delta >= 0 else "少"
    verb = "增至" if two_delta >= 0 else "降至"
    return (
        f"{cn_md(day)} Binance 在 Tron 上的 USDT {verb} {fmt_usd(total)}，较{cn_md(str(base['day']))} {fmt_pct(two)}。"
        f"据吴说数据中心统计，当日 Binance 在 Tron 网络标注地址的 USDT 合计为 {fmt_usd(total)}，"
        f"较前一日（{cn_md(str(prev['day']))}）{word} {fmt_usd(abs(delta))}（环比 {fmt_pct(dod)}），"
        f"较{cn_md(str(base['day']))}{two_word} {fmt_usd(abs(two_delta))}（{fmt_pct(two)}），"
        f"近 5 日均值 {fmt_usd(avg)}。"
        f"{why}"
        "口径为 DefiLlama 标注地址合计，不是 CryptoQuant，也不等于已经确认的损失金额。"
        f"\n{PAGE}\n{CHART}"
    )


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


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": []}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": []}
    ids = doc.get("sentIds") if isinstance(doc, dict) else None
    if isinstance(ids, list):
        return {"sentIds": [item for item in ids if isinstance(item, str)]}
    return {"sentIds": []}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        series = load_series()
    except (OSError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    latest = series[-1]
    prev = series[-2]
    base = series[-3]
    day = str(latest["day"])
    total = float(latest["usdt_usd"])
    prev_total = float(prev["usdt_usd"])
    base_total = float(base["usdt_usd"])
    dod = (total - prev_total) / prev_total * 100.0 if prev_total else None
    two = (total - base_total) / base_total * 100.0 if base_total else None
    age = (datetime.now(timezone.utc).date() - datetime.strptime(day, "%Y-%m-%d").date()).days
    prior_two = []
    for idx in range(2, len(series) - 1):
        now = float(series[idx]["usdt_usd"])
        old = float(series[idx - 2]["usdt_usd"])
        if old > 0:
            prior_two.append((now - old) / old * 100.0)
    worst_two = isinstance(two, float) and prior_two and two < min(prior_two)
    fire = (isinstance(dod, float) and dod <= DROP_1D) or (isinstance(two, float) and two <= DROP_2D)
    print(f"[binance-tron-usdt] day={day} total={total:.0f} dod={dod} two={two} worst2={worst_two}")
    if age > FRESH_DAYS:
        print(f"[skip] {day} 过旧 age={age}")
        return 0
    if not fire and not args.force:
        print("[skip] Tron USDT 没有出现单日或两日骤降")
        return 0
    if worst_two and isinstance(two, float):
        why = f"这两日降幅为 {fmt_pct(two)}，大于有统计以来其余日期。"
    elif isinstance(dod, float) and dod <= DROP_1D:
        why = f"单日降幅达到 {fmt_pct(dod)}。"
    elif isinstance(two, float):
        why = f"两日降幅达到 {fmt_pct(two)}。"
    else:
        why = ""
    window = [float(row["usdt_usd"]) for row in series[-5:]]
    text = build_news(latest, prev, base, sum(window) / len(window), why)
    msg_id = f"binance-tron-usdt-drop-{day}"
    print("---")
    print(msg_id)
    print(as_news(text))
    if not args.push:
        print("\n[dry-run] 未推送")
        return 0
    state = load_state()
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
        return 0
    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 1
    try:
        send_lark(webhook, secret, text)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] Lark 失败: {exc}", file=sys.stderr)
        return 1
    state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-60:]
    save_state(state)
    print(f"[pushed] {msg_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
