# -*- coding: utf-8 -*-
"""Robinhood Chain vs Base 日交易笔数 → Lark。

数据：growthepie txcount（rh_base_tx_download.py）。

只发最新一个数据日，且该日必须是近几天才收完的。历史越线不补发。
正文是可直接发稿的新闻，不写警报说明。刚越过 Base 的那一天只发越线稿，不另发日报。
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
JSON_CANDIDATES = [
    HERE / "output" / "json" / "rh_base_txcount.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "robinhood-chain" / "rh_base_txcount.json",
    Path("/root/dashboard/public/json/robinhood-chain/rh_base_txcount.json"),
]
STATE_PATH = HERE / "output" / "rh_base_tx_lark.json"
LOG_PATH = HERE / "rh_base_tx_flash.log"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

TZ_CN = timezone(timedelta(hours=8))
PAGE = "https://data.wublock123.com/dashboard/robinhood-chain"
FRESH_DAYS = 3


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

def load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def merge_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in ENV_CANDIDATES:
        env.update(load_env_file(path))
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def resolve_json(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    for path in JSON_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 rh_base_txcount.json，请先跑 rh_base_tx_download.py")


def load_rows(path: Path) -> list[dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = doc.get("data") if isinstance(doc, dict) else doc
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("无 data")
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = str(row.get("day") or "")[:10]
        try:
            rh = float(row["robinhood_txns"])
            base = float(row["base_txns"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"day": day, "robinhood_txns": rh, "base_txns": base, "rh_gt_base": rh > base})
    out.sort(key=lambda r: r["day"])
    return out


def wan(n: float) -> str:
    return f"{n / 1e4:.1f} 万笔"


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": [], "updatedAt": ""}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": [], "updatedAt": ""}
    ids = doc.get("sentIds") if isinstance(doc, dict) else None
    return {
        "sentIds": [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else [],
        "updatedAt": str((doc or {}).get("updatedAt") or "") if isinstance(doc, dict) else "",
    }


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def gen_sign(secret: str, ts: str) -> str:
    digest = hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    text = as_news(text)
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = gen_sign(secret, ts)
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
            raise RuntimeError(result)


def append_log(stamp: str, msg_id: str, ok: bool, text: str, extra: str = "") -> None:
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} id={msg_id} ok={ok} {extra}\n{text}\n\n")


def is_crossing(rows: list[dict[str, Any]], idx: int) -> bool:
    row = rows[idx]
    if not row["rh_gt_base"]:
        return False
    if idx == 0:
        return True
    return not rows[idx - 1]["rh_gt_base"]


def date_cn(day: str) -> str:
    year, month, day_num = day.split("-")
    return f"{int(year)} 年 {int(month)} 月 {int(day_num)} 日"


def build_news(row: dict[str, Any], *, crossed: bool) -> str:
    rh, base = row["robinhood_txns"], row["base_txns"]
    if crossed:
        title = "Robinhood Chain 日交易笔数超过 Base"
        compare = f"刚超过 Base 的约 {wan(base)}"
    elif rh > base:
        title = "Robinhood Chain 日交易笔数超过 Base"
        compare = f"超过 Base 的约 {wan(base)}"
    elif rh < base:
        title = "Robinhood Chain 日交易笔数低于 Base"
        compare = f"低于 Base 的约 {wan(base)}"
    else:
        title = "Robinhood Chain 日交易笔数与 Base 持平"
        compare = f"与 Base 持平，约为 {wan(base)}"
    return (
        f"{title}\n"
        f"吴说获悉，据 GrowThePie 数据，{date_cn(row['day'])} "
        f"Robinhood Chain 日交易约 {wan(rh)}，{compare}。\n"
        f"{PAGE}"
    )


def is_fresh(day: str, now: datetime | None = None) -> bool:
    current = (now or datetime.now(timezone.utc)).date()
    age = (current - datetime.strptime(day, "%Y-%m-%d").date()).days
    return 0 <= age <= FRESH_DAYS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--history-scan", action="store_true", help="已关闭，传入也不补发旧日期")
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    if getattr(args, "history_scan", False):
        args.history_scan = False

    try:
        path = resolve_json(args.json or None)
        rows = load_rows(path)
    except (OSError, RuntimeError, FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    latest = rows[-1]
    day = latest["day"]
    print(f"[rh-base-tx] json={path} latest={day} rh={latest['robinhood_txns']:.0f} base={latest['base_txns']:.0f}")

    state = load_state()
    sent = set(state["sentIds"])
    briefs: list[dict[str, Any]] = []

    if args.history_scan:
        print("[rh-base-tx] 已关闭历史回扫，不补发旧日期")

    if not is_fresh(day):
        print(f"[rh-base-tx] {day} 不是最近 {FRESH_DAYS} 天，不发")
        return 0

    idx_latest = len(rows) - 1
    crossed = is_crossing(rows, idx_latest)
    briefs.append(
        {
            "id": f"rh-base-tx-{'cross' if crossed else 'daily'}-{day}",
            "kind": "cross" if crossed else "daily",
            "text": build_news(latest, crossed=crossed),
        }
    )

    for brief in briefs:
        already = brief["id"] in sent
        print("---")
        print(f"id={brief['id']} kind={brief['kind']} already={already}")
        print(brief["text"])

    if not args.push:
        print("\n[dry-run]")
        return 0

    env = merge_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2

    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    sent_any = False
    for brief in briefs:
        if brief["id"] in sent and not args.force:
            print(f"skip {brief['id']}")
            continue
        text = brief["text"]
        try:
            send_lark(webhook, secret, text)
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            print(f"[ERROR] Lark {brief['id']}: {exc}", file=sys.stderr)
            append_log(stamp, brief["id"], False, text, extra=str(exc))
            return 1
        print(f"  → Lark OK {brief['id']}")
        if brief["id"] not in state["sentIds"]:
            state["sentIds"].append(brief["id"])
        sent_any = True
        append_log(stamp, brief["id"], True, text)
        time.sleep(0.4)

    if sent_any:
        state["sentIds"] = state["sentIds"][-120:]
        save_state(state)
        print(f"state → {STATE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
