# -*- coding: utf-8 -*-
"""上市公司 BTC 财库合计持仓 → Lark 新闻体。

读看板 overview_bitcoin.json（CoinGecko Treasuries），加总公司持仓、
占 2100 万枚上限比例、前五名。非 BitcoinTreasuries.net。

只发最新一份；总表过期不发；同一天只发一次。
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
JSON_DIRS = [
    Path("/root/dashboard/public/json/crypto-treasuries"),
    HERE / "output" / "json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "crypto-treasuries",
]
STATE_PATH = HERE / "output" / "corp_btc_holdings_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
PAGE = "https://data.wublock123.com/dashboard/treasuries"
MAX_SUPPLY = 21_000_000.0
TOP_N = 5
# 财库总表多为周更；过旧不发，避免滞后日常
FRESH_DAYS = 10


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


def resolve_json() -> Path:
    for folder in JSON_DIRS:
        path = folder / "overview_bitcoin.json"
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 overview_bitcoin.json")


def zh_date(day: str) -> str:
    y, m, d = day.split("-")
    return f"{int(y)} 年 {int(m)} 月 {int(d)} 日"


def fmt_btc(n: float) -> str:
    if n >= 1000:
        return f"{n:,.0f}"
    return f"{n:,.2f}"


def load_snapshot(path: Path, now: datetime) -> tuple[str, float, float, list[tuple[str, float]], int]:
    mtime = path.stat().st_mtime
    age_days = (now - datetime.fromtimestamp(mtime, timezone.utc)).total_seconds() / 86400
    day = datetime.fromtimestamp(mtime, timezone.utc).strftime("%Y-%m-%d")
    if age_days > FRESH_DAYS:
        print(f"[skip] overview_bitcoin {day} 已过 {age_days:.1f} 天")
        return day, 0.0, 0.0, [], 0

    doc = json.loads(path.read_text(encoding="utf-8"))
    data = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(data, dict):
        data = doc if isinstance(doc, dict) else {}
    companies = data.get("companies") if isinstance(data.get("companies"), list) else []
    rows: list[tuple[str, float]] = []
    for row in companies:
        if not isinstance(row, dict):
            continue
        if str(row.get("type") or "").lower() not in ("", "company"):
            continue
        try:
            amt = float(row.get("total_holdings") or 0)
        except (TypeError, ValueError):
            continue
        if amt <= 0:
            continue
        name = str(row.get("name") or row.get("symbol") or "未披露").strip()
        rows.append((name, amt))
    rows.sort(key=lambda x: -x[1])
    total = sum(a for _, a in rows)
    share = (total / MAX_SUPPLY * 100.0) if MAX_SUPPLY else 0.0
    return day, total, share, rows[:TOP_N], len(rows)


def build_news(
    day: str,
    total: float,
    share: float,
    top: list[tuple[str, float]],
    n_cos: int,
) -> str:
    top_s = "、".join(f"{name} {fmt_btc(amt)} 枚" for name, amt in top)
    return (
        f"据吴说数据中心统计，截至 {zh_date(day)}，CoinGecko 样本中上市公司比特币财库合计持有约 "
        f"{fmt_btc(total)} 枚 BTC（样本 {n_cos} 家），约占 2100 万枚上限的 {share:.2f}%。"
        f"持仓前五为 {top_s}。"
        "口径为 CoinGecko Treasuries 公司表加总，非 BitcoinTreasuries.net Top100。"
        f"\n{PAGE}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    day, total, share, top, n_cos = load_snapshot(resolve_json(), now)
    if total <= 0 or not top:
        return 0
    msg_id = f"corp-btc-holdings-{day}"
    text = build_news(day, total, share, top, n_cos)
    print(msg_id)
    print(text)
    if not args.push:
        print("[dry-run] 未推送")
        return 0
    state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.is_file() else {}
    sent = [x for x in state.get("sentIds", []) if isinstance(x, str)]
    if msg_id in sent:
        print("[skip] already", msg_id)
        return 0
    conf = env()
    webhook = (conf.get("LARK_ALERT_WEBHOOK") or "").strip()
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    send(webhook, (conf.get("LARK_ALERT_SECRET") or "").strip() or None, text)
    sent.append(msg_id)
    state["sentIds"] = sent[-40:]
    state["updatedAt"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("[pushed]", msg_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
