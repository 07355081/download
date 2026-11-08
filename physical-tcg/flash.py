#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Physical TCG 近 30 日协议费用创序列新高 → Lark 新闻。

DefiLlama overview/fees 取当前类别成员，summary/fees 取日费用。
只在最新完整日的近 30 日合计高于此前所有窗口、且日龄不超过 3 天时发一条。
不回扫旧高点。

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
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_PATH = HERE / "output" / "json" / "physical_tcg.json"
STATE_PATH = HERE / "output" / "physical_tcg_lark.json"
LOG_PATH = HERE / "flash.log"
JSON_URL = "https://data.wublock123.com/dashboard/lark-feeds/physical-tcg"
DASHBOARD_URL = "https://data.wublock123.com/dashboard/lark-feeds/physical-tcg"
API = "https://api.llama.fi"
CATEGORY = "Physical TCG"
UA = "dashboard-data/physical-tcg"
TZ_CN = timezone(timedelta(hours=8))
WINDOW = 30
FRESH_DAYS = 3
TOP_N = 5
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


def gen_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
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
            raise RuntimeError(f"Lark 返回异常: {result}")


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


def tcg_series(days: dict[str, Any], ranked: list[tuple[str, float]], names: dict[str, str]) -> list[dict[str, Any]]:
    dates = sorted(days)[-180:]
    series: list[dict[str, Any]] = []
    total = []
    for day in dates:
        row = days.get(day)
        if not isinstance(row, dict):
            continue
        total.append({
            "month": day,
            "usd": round(sum(v for v in row.values() if isinstance(v, (int, float))), 2),
        })
    if len(total) >= 2:
        series.append({"label": "当日费用合计", "labelEn": "Daily fees", "months": total})
    for slug, _val in ranked[:4]:
        points = []
        for day in dates:
            row = days.get(day)
            if not isinstance(row, dict) or not isinstance(row.get(slug), (int, float)):
                continue
            points.append({"month": day, "usd": round(float(row[slug]), 2)})
        if len(points) >= 2:
            name = names.get(slug, slug)
            series.append({"label": name, "labelEn": name, "months": points})
    return series


def window_rank(protocols: list[dict[str, Any]], key: str, limit: int = 8) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in protocols:
        slug = row.get("slug")
        val = row.get(key)
        if not isinstance(slug, str) or not isinstance(val, (int, float)) or val <= 0:
            continue
        rows.append({
            "slug": slug,
            "name": str(row.get("displayName") or row.get("name") or slug),
            "usd": round(float(val), 2),
        })
    rows.sort(key=lambda item: -item["usd"])
    return rows[:limit]


def sum_metric(protocols: list[dict[str, Any]], key: str) -> float:
    total = 0.0
    for row in protocols:
        val = row.get(key)
        if isinstance(val, (int, float)):
            total += float(val)
    return round(total, 2)


def ingest_chart(doc: dict[str, Any], days: dict[str, dict[str, float]], slug: str) -> None:
    for pt in doc.get("totalDataChart") or []:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        try:
            day = datetime.fromtimestamp(int(pt[0]), tz=timezone.utc).strftime("%Y-%m-%d")
            val = float(pt[1] or 0)
        except (TypeError, ValueError, OSError):
            continue
        days.setdefault(day, {})[slug] = val


def month_points(days: dict[str, dict[str, float]], slugs: set[str]) -> list[dict[str, Any]]:
    buckets: dict[str, float] = {}
    for day, row in days.items():
        if not isinstance(row, dict) or len(day) < 7:
            continue
        total = 0.0
        for slug, val in row.items():
            if slug in slugs and isinstance(val, (int, float)):
                total += float(val)
        if total:
            buckets[day[:7]] = buckets.get(day[:7], 0.0) + total
    return [{"month": key, "usd": round(val, 2)} for key, val in sorted(buckets.items())]


def load_gecko_env() -> None:
    path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        text = line.strip().lstrip("\ufeff")
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def gecko_json(path: str) -> Any:
    load_gecko_env()
    key = (
        os.environ.get("COINGECKO_DEMO_API_KEY")
        or os.environ.get("COINGECKO_KEY")
        or os.environ.get("COINGECKO_API_KEY")
        or ""
    ).strip()
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if key:
        headers["x-cg-demo-api-key"] = key
    url = "https://api.coingecko.com/api/v3" + path
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode("utf-8"))


def pair_points(pairs: Any, ndigits: int) -> list[dict[str, Any]]:
    by_day: dict[str, float] = {}
    if not isinstance(pairs, list):
        return []
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        try:
            day = datetime.fromtimestamp(float(pair[0]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            by_day[day] = round(float(pair[1]), ndigits)
        except (TypeError, ValueError, OSError):
            continue
    return [{"month": day, "usd": val} for day, val in sorted(by_day.items())]


def load_volume() -> tuple[dict[str, str], dict[str, dict[str, float]], list[dict[str, Any]]]:
    try:
        overview = http_json(
            API + "/overview/dexs?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true"
        )
    except RuntimeError as exc:
        print(f"[warn] 成交额总表: {exc}")
        return {}, {}, []
    protocols = [
        p for p in (overview.get("protocols") or [])
        if isinstance(p, dict) and p.get("category") == CATEGORY and isinstance(p.get("slug"), str)
    ]
    names = {
        p["slug"]: str(p.get("displayName") or p.get("name") or p["slug"])
        for p in protocols
    }
    days: dict[str, dict[str, float]] = {}
    for slug in sorted(names):
        url = API + "/summary/dexs/" + urllib.parse.quote(slug, safe=".-_")
        try:
            doc = http_json(url)
        except RuntimeError as exc:
            print(f"[warn] 成交额 {slug}: {exc}")
            continue
        ingest_chart(doc, days, slug)
        time.sleep(0.12)
    return names, days, window_rank(protocols, "total24h")


def load_cards_token() -> dict[str, Any] | None:
    try:
        spot = gecko_json(
            "/coins/collector-crypt?localization=false&tickers=false&community_data=false&developer_data=false"
        )
        chart = gecko_json("/coins/collector-crypt/market_chart?vs_currency=usd&days=180&interval=daily")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        print(f"[warn] CoinGecko CARDS: {exc}")
        return None
    market = spot.get("market_data") if isinstance(spot, dict) else None
    if not isinstance(market, dict) or not isinstance(chart, dict):
        return None
    price = (market.get("current_price") or {}).get("usd")
    mcap = (market.get("market_cap") or {}).get("usd")
    volume = (market.get("total_volume") or {}).get("usd")
    return {
        "symbol": "CARDS",
        "name": "Collector Crypt",
        "price_usd": price if isinstance(price, (int, float)) else None,
        "mcap_usd": mcap if isinstance(mcap, (int, float)) else None,
        "volume_24h_usd": volume if isinstance(volume, (int, float)) else None,
        "price_series": pair_points(chart.get("prices"), 6),
        "mcap_series": pair_points(chart.get("market_caps"), 2),
    }


def http_json(url: str) -> Any:
    last: Exception | None = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last = exc
            if attempt < 3:
                time.sleep(min(2**attempt, 8))
    raise RuntimeError(f"GET {url} failed: {last}")


def load_store() -> dict[str, dict[str, float]]:
    if not OUT_PATH.is_file():
        return {}
    try:
        doc = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = doc.get("days") if isinstance(doc, dict) else None
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, float]] = {}
    for day, row in raw.items():
        if not isinstance(day, str) or not isinstance(row, dict):
            continue
        cleaned = {
            str(slug): float(val)
            for slug, val in row.items()
            if isinstance(slug, str) and isinstance(val, (int, float))
        }
        if cleaned:
            out[day] = cleaned
    return out


def wan(usd: float) -> str:
    if abs(usd) >= 1e8:
        return f"{usd / 1e8:.2f} 亿美元"
    return f"{usd / 1e4:.0f} 万美元"


def cn_day(day: str) -> str:
    y, m, d = day.split("-")
    return f"{int(y)}年{int(m)}月{int(d)}日"


def window_sum(days: dict[str, dict[str, float]], end: str, slugs: set[str]) -> tuple[float, dict[str, float]]:
    end_d = datetime.strptime(end, "%Y-%m-%d").date()
    total = 0.0
    parts: dict[str, float] = {}
    for i in range(WINDOW):
        key = (end_d - timedelta(days=i)).isoformat()
        for slug, val in days.get(key, {}).items():
            if slug not in slugs:
                continue
            total += val
            parts[slug] = parts.get(slug, 0.0) + val
    return total, parts


def build_news(
    latest: str,
    total: float,
    parts: dict[str, float],
    names: dict[str, str],
    prior_day: str | None,
    prior_high: float | None,
) -> str:
    ranked = sorted(parts.items(), key=lambda kv: -kv[1])[:TOP_N]
    bits = [f"{names.get(slug, slug)} 约 {wan(val)}" for slug, val in ranked if val > 0]
    detail = "、".join(bits)
    prior = ""
    if prior_day and prior_high is not None:
        prior = f"高于此前高点（{cn_day(prior_day)}，约 {wan(prior_high)}）。"
    return (
        "【历史阈值警报】\n"
        f"据吴说数据中心统计，截至{cn_day(latest)}，Physical TCG 赛道过去 30 天协议费用合计约 {wan(total)}，"
        f"创本通道记录新高。{prior}"
        f"其中 {detail}。"
        f"口径为 DefiLlama 协议费用日序列近 30 个完整日合计。"
        f"\n{DASHBOARD_URL}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    try:
        overview = http_json(
            API + "/overview/fees?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true"
        )
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    protocols = [
        p for p in (overview.get("protocols") or [])
        if isinstance(p, dict) and p.get("category") == CATEGORY and isinstance(p.get("slug"), str)
    ]
    if not protocols:
        print("[ERROR] Physical TCG 名单为空", file=sys.stderr)
        return 1
    names = {
        p["slug"]: str(p.get("displayName") or p.get("name") or p["slug"])
        for p in protocols
    }
    slugs = set(names)
    days = load_store()
    for slug in sorted(slugs):
        url = API + "/summary/fees/" + urllib.parse.quote(slug, safe=".-_")
        try:
            doc = http_json(url)
        except RuntimeError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 1
        ingest_chart(doc, days, slug)
        time.sleep(0.15)

    today = datetime.now(timezone.utc).date()
    ends = sorted(day for day in days if day < today.isoformat())
    if not ends:
        print("[skip] 没有已完整的日期")
        return 0
    latest = ends[-1]
    latest_total, latest_parts = window_sum(days, latest, slugs)
    prior_ends = [day for day in ends if day < latest]
    prior_best: tuple[str, float] | None = None
    for day in prior_ends:
        total, _ = window_sum(days, day, slugs)
        if prior_best is None or total > prior_best[1]:
            prior_best = (day, total)
    prior_high = prior_best[1] if prior_best else None
    is_ath = prior_high is not None and latest_total > prior_high + 1.0
    age = (today - datetime.strptime(latest, "%Y-%m-%d").date()).days

    ranked = sorted(latest_parts.items(), key=lambda kv: -kv[1])
    payload = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "DefiLlama summary/fees",
        "category": CATEGORY,
        "latest_day": latest,
        "latest_age_days": age,
        "latest_30d_usd": round(latest_total, 2),
        "ath_day": latest if is_ath else (prior_best[0] if prior_best else latest),
        "ath_30d_usd": round(latest_total if is_ath or prior_high is None else prior_high, 2),
        "is_ath": is_ath,
        "top": [
            {"slug": slug, "name": names.get(slug, slug), "fees_30d_usd": round(val, 2)}
            for slug, val in ranked[:12]
        ],
        "days": {day: {slug: round(val, 2) for slug, val in row.items()} for day, row in sorted(days.items())},
        "series": tcg_series(days, ranked, names),
        "fee_windows": {
            "h24": sum_metric(protocols, "total24h"),
            "d7": sum_metric(protocols, "total7d"),
            "d30": round(latest_total, 2),
            "y1": sum_metric(protocols, "total1y"),
            "all_time": sum_metric(protocols, "totalAllTime"),
        },
        "months": [row for row in month_points(days, slugs) if row["month"] < today.strftime("%Y-%m")],
        "fee_24h": window_rank(protocols, "total24h"),
    }
    closed_month = today.strftime("%Y-%m")
    volume_names, volume_days, volume_24h = load_volume()
    if volume_24h:
        payload["volume_24h"] = volume_24h
    if volume_names and volume_days:
        vol_ends = sorted(day for day in volume_days if day < today.isoformat())
        if vol_ends:
            vol_latest = vol_ends[-1]
            vol_total, vol_parts = window_sum(volume_days, vol_latest, set(volume_names))
            vol_ranked = sorted(vol_parts.items(), key=lambda kv: -kv[1])
            payload["volume_day"] = vol_latest
            payload["volume_30d_usd"] = round(vol_total, 2)
            payload["volume_top"] = [
                {"slug": slug, "name": volume_names.get(slug, slug), "usd": round(val, 2)}
                for slug, val in vol_ranked[:8]
                if val > 0
            ]
            payload["volume_series"] = tcg_series(volume_days, vol_ranked, volume_names)
            if payload["volume_series"]:
                payload["volume_series"][0]["label"] = "当日成交额合计"
                payload["volume_series"][0]["labelEn"] = "Daily volume"
            payload["volume_months"] = [
                row for row in month_points(volume_days, set(volume_names))
                if row["month"] < closed_month
            ]
    token = load_cards_token()
    if token:
        payload["token"] = {
            "symbol": token["symbol"],
            "name": token["name"],
            "price_usd": token["price_usd"],
            "mcap_usd": token["mcap_usd"],
            "volume_24h_usd": token["volume_24h_usd"],
        }
        payload["token_price_series"] = token["price_series"]
        payload["token_mcap_series"] = token["mcap_series"]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(
        f"[physical-tcg] latest={latest} age={age}d fees30={latest_total:.0f} "
        f"vol30={payload.get('volume_30d_usd')} "
        f"cards={None if token is None else token.get('price_usd')} "
        f"ath={is_ath}"
    )

    fresh = age <= FRESH_DAYS
    if not args.force and (not is_ath or not fresh):
        why = "未创近 30 日费用新高" if not is_ath else f"最新日龄 {age} 天，超过 {FRESH_DAYS} 天"
        print(f"[skip] {why}")
        return 0

    prior_day = prior_best[0] if prior_best else None
    raw = build_news(latest, latest_total, latest_parts, names, prior_day, prior_high)
    text = as_news(raw)
    msg_id = f"physical-tcg-30d-ath-{latest}"
    print("---")
    print(f"id={msg_id}")
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
    state = load_state()
    if msg_id in state["sentIds"] and not args.force:
        print(f"[skip] already {msg_id}")
        return 0
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    try:
        send_lark(webhook, secret, raw)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Lark 失败: {exc}", file=sys.stderr)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"=== {stamp} {msg_id} ok=False {exc}\n{text}\n\n")
        return 1
    print(f"[pushed] {msg_id}")
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-120:]
    save_state(state)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {stamp} {msg_id} ok=True\n{text}\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
