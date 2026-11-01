# -*- coding: utf-8 -*-
"""加密风投融资额度档位份额（RootData 明细加总）→ Lark。

口径：已有 fundraising_deals.json 中有金额的轮次，按自然年统计
300 万–1000 万美元档占全年已知金额轮次的份额，以及 >1000 万美元份额。
非 CryptoRank 同源，数字可能对不齐。

只发最新一个已收完自然年，不回扫更早年份；收盘超过 90 天不再发（避免滞后）。
- 较上一年中档份额变化 ≤ -5pp，或大额档 ≥ +5pp →【历史阈值警报】
- 否则仍发一条年度新闻（首行不加日常/阈值标记，正文可发稿）

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
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JSON_CANDIDATES = [
    ROOT / "financing-news" / "output" / "json" / "fundraising_deals.json",
    ROOT.parent / "data-dashboard" / "public" / "json" / "financing-news" / "fundraising_deals.json",
    Path("/root/dashboard/public/json/financing-news/fundraising_deals.json"),
]
STATE_PATH = HERE / "output" / "financing_deal_bucket_lark.json"
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
PAGE = "https://data.wublock123.com/dashboard/financing-news"
MID_LO = 3_000_000.0
MID_HI = 10_000_000.0
YOY_PP = 5.0
FRESH_DAYS = 90
LARK_THRESHOLD = "【历史阈值警报】"


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
    raise FileNotFoundError("找不到 fundraising_deals.json")


def load_rows(path: Path) -> list[dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = doc.get("data") if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        raise RuntimeError("invalid fundraising_deals.json")
    return [row for row in rows if isinstance(row, dict)]


def year_buckets(rows: list[dict[str, Any]]) -> dict[int, Counter]:
    out: dict[int, Counter] = {}
    for row in rows:
        day = str(row.get("date") or "")[:10]
        if len(day) < 4 or not day[:4].isdigit():
            continue
        year = int(day[:4])
        amt = row.get("amount")
        if not isinstance(amt, (int, float)) or float(amt) <= 0:
            continue
        a = float(amt)
        bucket = out.setdefault(year, Counter())
        if a < MID_LO:
            bucket["lt3"] += 1
        elif a <= MID_HI:
            bucket["mid"] += 1
        else:
            bucket["gt10"] += 1
    return out


def share(counter: Counter, key: str) -> float | None:
    tot = counter["lt3"] + counter["mid"] + counter["gt10"]
    if tot <= 0:
        return None
    return counter[key] / tot * 100.0


def fmt_pp(n: float) -> str:
    sign = "+" if n > 0 else ""
    return f"{sign}{n:.1f} 个百分点"


def build_news(
    year: int,
    cur: Counter,
    prev: Counter | None,
    *,
    mid: float,
    big: float,
    mid_yoy: float | None,
    big_yoy: float | None,
) -> str:
    tot = cur["lt3"] + cur["mid"] + cur["gt10"]
    bits = [
        f"{year} 年 RootData 样本中有金额的加密融资轮次共 {tot} 笔，"
        f"其中 300 万–1000 万美元档约 {cur['mid']} 笔、占 {mid:.1f}%，"
        f"1000 万美元以上约 {cur['gt10']} 笔、占 {big:.1f}%"
    ]
    if mid_yoy is not None and prev is not None:
        bits.append(f"中档份额较 {year - 1} 年 {fmt_pp(mid_yoy)}")
    if big_yoy is not None and prev is not None:
        bits.append(f"大额档份额较 {year - 1} 年 {fmt_pp(big_yoy)}")
    body = "；".join(bits) + "。"
    return (
        f"据吴说数据中心统计，{body}"
        "口径为 RootData 融资明细按轮次金额加总，非 CryptoRank。"
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


def gen_lark_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    body = as_news(text)
    if text.startswith(LARK_THRESHOLD):
        body = f"{LARK_THRESHOLD}\n{body}"
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": body}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = gen_lark_sign(secret, ts)
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
    parser.add_argument(
        "--allow-stale",
        action="store_true",
        help="调试：忽略收盘 90 天新鲜度",
    )
    args = parser.parse_args()

    path = resolve_json(args.json)
    buckets = year_buckets(load_rows(path))
    today = date.today()
    complete = sorted(y for y in buckets if y < today.year and (buckets[y]["lt3"] + buckets[y]["mid"] + buckets[y]["gt10"]) >= 30)
    if not complete:
        print("[financing-deal-bucket] 无已收完自然年")
        return 0
    year = complete[-1]
    cur = buckets[year]
    mid = share(cur, "mid")
    big = share(cur, "gt10")
    if mid is None or big is None:
        print(f"[financing-deal-bucket] {year} 无法算份额")
        return 0

    year_end = date(year, 12, 31)
    age = (today - year_end).days
    if age > FRESH_DAYS and not args.allow_stale:
        print(f"[financing-deal-bucket] {year} 已收盘 {age} 天，超过 {FRESH_DAYS} 天，不发（避免滞后）")
        return 0

    prev = buckets.get(year - 1)
    mid_yoy = big_yoy = None
    if prev is not None:
        pm, pb = share(prev, "mid"), share(prev, "gt10")
        if pm is not None:
            mid_yoy = mid - pm
        if pb is not None:
            big_yoy = big - pb

    notable = (mid_yoy is not None and mid_yoy <= -YOY_PP) or (big_yoy is not None and big_yoy >= YOY_PP)
    news = build_news(year, cur, prev, mid=mid, big=big, mid_yoy=mid_yoy, big_yoy=big_yoy)
    msg_id = f"financing-deal-bucket-{year}"
    text = f"{LARK_THRESHOLD}\n{news}" if notable else news

    print(f"[financing-deal-bucket] year={year} mid={mid:.1f}% big={big:.1f}% mid_yoy={mid_yoy} big_yoy={big_yoy} notable={notable} age={age}")
    print("---")
    print(f"id={msg_id}")
    print(text)

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
        return 2

    try:
        send_lark(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Lark 失败: {exc}", file=sys.stderr)
        return 1

    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-80:]
    save_state(state)
    print(f"[pushed] {msg_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
