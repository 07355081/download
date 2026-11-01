# -*- coding: utf-8 -*-
"""Hyperliquid 季度成交 + 非 Crypto 品类占比 → Lark。

读看板已有 JSON，不新跑 Dune。
只发最新一个完整 UTC 季；季结束后超过 45 天不发；缺季末日（允许 Dune 滞后 3 天）不发。
不回扫旧季。正文是新闻，不是警报。
非 Crypto（股票/外汇/商品等）作 Token Terminal「RWA」近邻，看板没有单独 RWA 栏。
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
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
VOL_CANDIDATES = [
    HERE / "output" / "json" / "hl_volume_split.json",
    Path("/root/dashboard/public/json/hyperliquid/hl_volume_split.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "hyperliquid" / "hl_volume_split.json",
]
CAT_CANDIDATES = [
    HERE / "output" / "json" / "hl_by_category.json",
    Path("/root/dashboard/public/json/hyperliquid/hl_by_category.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "hyperliquid" / "hl_by_category.json",
]
# hl_by_category(8077484) 若空，用 HIP-3 品类表作非 Crypto 分子（与历史抽检同量级）
HIP3_CAT_CANDIDATES = [
    HERE / "output" / "json" / "hip3_by_category.json",
    Path("/root/dashboard/public/json/hyperliquid/hip3_by_category.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "hyperliquid" / "hip3_by_category.json",
]
STATE_PATH = HERE / "output" / "hl_quarter_rwa_lark.json"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/hyperliquid"
VOL_URL = "https://data.wublock123.com/json/hyperliquid/hl_volume_split.json"
CAT_URL = "https://data.wublock123.com/json/hyperliquid/hl_by_category.json"
TZ_CN = timezone(timedelta(hours=8))
FRESH_DAYS = 45
END_LAG_DAYS = 3  # Dune 缓存常慢几天，季末日后仍认完整
RWA_CATS = frozenset({"Stocks", "Bonds", "Commodities", "FX", "Indices/ETFs", "Pre-IPO"})
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


def resolve_json(cands: list[Path]) -> Path:
    for path in cands:
        if path.is_file():
            return path
    raise FileNotFoundError(cands[0].name)


def load_rows(cands: list[Path], url: str) -> list[dict[str, Any]]:
    try:
        return load_json(resolve_json(cands))
    except FileNotFoundError:
        req = urllib.request.Request(url, headers={"User-Agent": "dashboard-data/hl-quarter-rwa"})
        with urllib.request.urlopen(req, timeout=40) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
        data = doc.get("data") if isinstance(doc, dict) else doc
        if not isinstance(data, list):
            raise RuntimeError(f"{url} 无 data")
        return data


def parse_day(raw: Any) -> date | None:
    text = str(raw or "")[:10]
    if len(text) != 10:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def quarter_of(day: date) -> tuple[int, int]:
    return day.year, (day.month - 1) // 3 + 1


def quarter_end(year: int, q: int) -> date:
    month = q * 3
    return date(year, month, monthrange(year, month)[1])


def quarter_label(year: int, q: int) -> str:
    return f"{year} 年 Q{q}"


def load_json(path: Path) -> Any:
    doc = json.loads(path.read_text(encoding="utf-8"))
    data = doc.get("data") if isinstance(doc, dict) else doc
    if not isinstance(data, list):
        raise RuntimeError(f"{path.name} 无 data")
    return data


def volume_by_day(rows: list[dict[str, Any]]) -> dict[date, float]:
    out: dict[date, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = parse_day(row.get("day") or row.get("Date"))
        if day is None:
            continue
        val = row.get("total_trading_volume_usd")
        if not isinstance(val, (int, float)):
            continue
        out[day] = float(val)
    return out


def category_by_day(rows: list[dict[str, Any]]) -> dict[date, dict[str, float]]:
    out: dict[date, dict[str, float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = parse_day(row.get("Date") or row.get("day"))
        if day is None:
            continue
        cat = str(row.get("Category") or "").strip()
        val = row.get("Daily Volume")
        if not cat or not isinstance(val, (int, float)):
            continue
        slot = out.setdefault(day, {})
        slot[cat] = slot.get(cat, 0.0) + float(val)
    return out


def sum_quarter(series: dict[date, float], year: int, q: int) -> tuple[float, date | None]:
    total = 0.0
    last: date | None = None
    start_month = (q - 1) * 3 + 1
    for day, val in series.items():
        if day.year == year and (day.month - 1) // 3 + 1 == q:
            total += val
            if last is None or day > last:
                last = day
    return total, last


def rwa_usd(cats: dict[date, dict[str, float]], year: int, q: int) -> tuple[float, date | None]:
    """非 Crypto 品类成交额（分子）；分母用 hl_volume_split 全站成交。"""
    rwa = 0.0
    last: date | None = None
    for day, slot in cats.items():
        if day.year != year or (day.month - 1) // 3 + 1 != q:
            continue
        rwa += sum(v for k, v in slot.items() if k in RWA_CATS)
        if last is None or day > last:
            last = day
    return rwa, last


def load_category_map() -> tuple[dict[date, dict[str, float]], str]:
    try:
        rows = load_rows(CAT_CANDIDATES, CAT_URL)
        if rows:
            return category_by_day(rows), "hl_by_category"
    except (FileNotFoundError, OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError):
        pass
    rows = load_json(resolve_json(HIP3_CAT_CANDIDATES))
    if not rows:
        raise RuntimeError("品类表为空（hl_by_category / hip3_by_category）")
    return category_by_day(rows), "hip3_by_category"


def yi(usd: float) -> str:
    return f"{usd / 1e8:.1f} 亿美元"


def pct(curr: float, prev: float) -> str | None:
    if prev <= 0:
        return None
    return f"{(curr / prev - 1) * 100:+.1f}%"


def build_news(
    year: int,
    q: int,
    vol: float,
    qoq: str | None,
    yoy: str | None,
    share: float,
) -> str:
    bits = [
        f"据吴说数据中心统计，{quarter_label(year, q)} Hyperliquid 成交约 {yi(vol)}",
    ]
    extras = [item for item in (f"较上季 {qoq}" if qoq else None, f"较去年同季 {yoy}" if yoy else None) if item]
    if extras:
        bits.append(f"（{'，'.join(extras)}）")
    bits.append("。")
    bits.append(
        f"同期股票、外汇、商品、债券、指数及盘前等非 Crypto 品类约占 {share * 100:.1f}%，"
        f"看板无单独 RWA 栏，此为近邻口径。"
    )
    bits.append(f"\n{DASHBOARD_URL}")
    return "".join(bits)


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


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": []}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": []}
    ids = doc.get("sentIds") if isinstance(doc, dict) else None
    return {"sentIds": [item for item in ids if isinstance(item, str)] if isinstance(ids, list) else []}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def day_ok(last: date | None, end: date) -> bool:
    if last is None:
        return False
    return last >= end - timedelta(days=END_LAG_DAYS) and last <= end


def pick_quarter(
    vol_map: dict[date, float],
    cat_map: dict[date, dict[str, float]],
    today: date,
) -> tuple[int, int, date, float, date | None, float, date | None] | None:
    """选已收盘且两端都齐（允许 END_LAG_DAYS）的最新一季。"""
    candidates: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for day in sorted(vol_map, reverse=True):
        key = quarter_of(day)
        if key in seen:
            continue
        seen.add(key)
        end = quarter_end(*key)
        if end < today:
            candidates.append(key)
    for year, q in candidates:
        end = quarter_end(year, q)
        vol, vol_last = sum_quarter(vol_map, year, q)
        rwa, cat_last = rwa_usd(cat_map, year, q)
        if day_ok(vol_last, end) and day_ok(cat_last, end) and rwa > 0 and vol > 0:
            return year, q, end, vol, vol_last, rwa, cat_last
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--allow-stale", action="store_true")
    args = parser.parse_args()

    vol_map = volume_by_day(load_rows(VOL_CANDIDATES, VOL_URL))
    try:
        cat_map, cat_src = load_category_map()
    except (FileNotFoundError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] 品类表失败: {exc}", file=sys.stderr)
        return 1
    if not vol_map:
        print("[ERROR] 成交序列为空", file=sys.stderr)
        return 1

    today = datetime.now(TZ_CN).date()
    picked = pick_quarter(vol_map, cat_map, today)
    if not picked:
        latest = max(vol_map)
        print(f"[skip] 没有完整已收盘季（最新日 {latest}，品类源 {cat_src}）")
        return 0
    year, q, end, vol, vol_last, rwa, cat_last = picked
    age = (today - end).days
    share = rwa / vol
    print(
        f"[hl-quarter-rwa] q={year}Q{q} end={end} cat_src={cat_src} "
        f"vol_last={vol_last} cat_last={cat_last} age={age}d vol={vol:.0f} share={share:.3f}"
    )
    if age > FRESH_DAYS and not args.allow_stale:
        print(f"[skip] 季结束已 {age} 天，超过 {FRESH_DAYS} 天，不补历史")
        return 0

    prev_y, prev_q = (year - 1, 4) if q == 1 else (year, q - 1)
    prev_vol, prev_last = sum_quarter(vol_map, prev_y, prev_q)
    yoy_vol, yoy_last = sum_quarter(vol_map, year - 1, q)
    qoq = pct(vol, prev_vol) if day_ok(prev_last, quarter_end(prev_y, prev_q)) else None
    yoy = pct(vol, yoy_vol) if day_ok(yoy_last, quarter_end(year - 1, q)) else None
    msg_id = f"hl-quarter-rwa-{year}Q{q}"
    text = build_news(year, q, vol, qoq, yoy, share)
    print(msg_id)
    print(as_news(text))

    state = load_state()
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] 已发 {msg_id}")
        return 0
    if not args.push:
        print("[dry-run] 未推送")
        return 0
    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    send_lark(webhook, secret, text)
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-20:]
    save_state(state)
    print(f"[pushed] {msg_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
