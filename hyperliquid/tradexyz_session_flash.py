# -*- coding: utf-8 -*-
"""TradeXYZ 近 90 日成交时段结构 → Lark 新闻。

免费源：Hyperliquid info candleSnapshot（dex=xyz，1h）。
- 纽交所常规时段：America/New_York 周一至周五 09:30–16:00
- 标的闭市：非加密标的在上述时段之外（含周末）；加密视为 24h 开市
- 只发最新一个已结束 UTC 日为截止的 90 日窗；不回扫
- 非 Artemis/Blockworks 专有口径
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
OUT_PATH = HERE / "output" / "json" / "tradexyz_session.json"
CACHE_PATH = HERE / "output" / "tradexyz_session_hours.json"
STATE_PATH = HERE / "output" / "tradexyz_session_lark.json"
INFO_URL = "https://api.hyperliquid.xyz/info"
DEX = "xyz"
WINDOW_DAYS = 90
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/tradexyz-session"
TZ_CN = timezone(timedelta(hours=8))
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]
# 加密：标的视为始终可交易
CRYPTO = {
    "BTC",
    "ETH",
    "SOL",
    "XRP",
    "BNB",
    "DOGE",
    "ADA",
    "AVAX",
    "LINK",
    "DOT",
    "LTC",
    "BCH",
    "SUI",
    "APT",
    "NEAR",
    "TON",
    "TRX",
    "HYPE",
    "PURR",
    "PURRDAT",
    "QNT",
    "BIRD",
    "BOT",
    "WIF",
    "PEPE",
    "BONK",
    "FARTCOIN",
}
# 商品 / 外汇 / 多数指数期货：近 24h，不计入「标的闭市」
ALWAYS_OPEN = {
    "GOLD",
    "SILVER",
    "COPPER",
    "PLATINUM",
    "PALLADIUM",
    "CL",
    "BRENTOIL",
    "NATGAS",
    "ALUMINIUM",
    "CORN",
    "WHEAT",
    "TTF",
    "URANIUM",
    "EUR",
    "JPY",
    "GBP",
    "KRW",
    "NOK",
    "DXY",
    "SP500",
    "XYZ100",
    "JP225",
    "NIFTY",
    "IBOV",
    "KR200",
    "VIX",
    "VOL",
    "NDX",
    "DJI",
}

sys.path.insert(0, str(ROOT))
from lark_copy import as_news  # noqa: E402

try:
    from zoneinfo import ZoneInfo

    NY = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001
    NY = timezone(timedelta(hours=-4))


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


def send_lark(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": as_news(text)}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(
            hmac.new(f"{ts}\n{secret}".encode(), digestmod=hashlib.sha256).digest()
        ).decode()
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", "replace")
    result = json.loads(body or "{}")
    if str(result.get("code", "0")) not in ("0", "200"):
        raise RuntimeError(body)


def info(body: dict[str, Any]) -> Any:
    data = json.dumps(body).encode("utf-8")
    last: Exception | None = None
    for attempt in range(4):
        req = urllib.request.Request(
            INFO_URL,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "dashboard-data/tradexyz-session"},
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in (429, 500, 502, 503):
                raise
            time.sleep(1.2 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"Hyperliquid info 失败: {last}")


def num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def coin_symbol(coin: str) -> str:
    return str(coin).split(":")[-1].upper()


def is_crypto(coin: str) -> bool:
    return coin_symbol(coin) in CRYPTO


def underlying_can_close(coin: str) -> bool:
    """仅美股/ETF 等跟现金股市的标的，在非纽交所 RTH 计为闭市。"""
    sym = coin_symbol(coin)
    if sym in CRYPTO or sym in ALWAYS_OPEN:
        return False
    return True


def in_nyse_rth(ts_ms: int) -> bool:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(NY)
    if dt.weekday() >= 5:
        return False
    minutes = dt.hour * 60 + dt.minute
    return (9 * 60 + 30) <= minutes < (16 * 60)


def list_xyz_coins() -> list[str]:
    payload = info({"type": "metaAndAssetCtxs", "dex": DEX})
    if not isinstance(payload, list) or len(payload) < 2:
        raise RuntimeError("metaAndAssetCtxs 异常")
    meta = payload[0] if isinstance(payload[0], dict) else {}
    universe = meta.get("universe") if isinstance(meta, dict) else None
    if not isinstance(universe, list):
        raise RuntimeError("universe 异常")
    coins: list[str] = []
    for asset in universe:
        if isinstance(asset, dict) and asset.get("name"):
            name = str(asset["name"])
            if not name.startswith("xyz:"):
                name = f"xyz:{name}"
            coins.append(name)
    if not coins:
        raise RuntimeError("无 xyz 市场")
    return coins


def xyz_oi_usd() -> float:
    payload = info({"type": "metaAndAssetCtxs", "dex": DEX})
    meta, ctxs = payload[0], payload[1]
    universe = (meta or {}).get("universe") if isinstance(meta, dict) else None
    if not isinstance(universe, list) or not isinstance(ctxs, list):
        return 0.0
    total = 0.0
    for _asset, ctx in zip(universe, ctxs):
        if isinstance(ctx, dict):
            total += num(ctx.get("openInterest")) * num(ctx.get("markPx"))
    return total


def load_cache() -> dict[str, Any]:
    if not CACHE_PATH.is_file():
        return {"hours": {}, "coins_done": {}}
    try:
        doc = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"hours": {}, "coins_done": {}}
    hours = doc.get("hours") if isinstance(doc.get("hours"), dict) else {}
    return {"hours": hours, "coins_done": {}}


def save_cache(hours: dict[str, dict[str, float]]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 只留窗口外再多 5 天，避免文件膨胀
    cutoff = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS + 5)).strftime("%Y-%m-%dT%H")
    kept = {k: v for k, v in hours.items() if k >= cutoff}
    payload = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hours": kept,
    }
    tmp = CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_PATH)


def fetch_hours(start: datetime, end: datetime) -> dict[str, dict[str, float]]:
    """UTC 小时键 → total / outside_nyse / closed_underlying 美元成交。"""
    cache = load_cache()
    hours: dict[str, dict[str, float]] = {
        k: dict(v) for k, v in (cache.get("hours") or {}).items() if isinstance(v, dict)
    }
    # 重算窗口：丢掉旧聚合后全量重拉（市场名单会变；日更一次可接受）
    start_key = start.strftime("%Y-%m-%dT%H")
    end_key = end.strftime("%Y-%m-%dT%H")
    hours = {k: v for k, v in hours.items() if k < start_key or k >= end_key}

    coins = list_xyz_coins()
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    print(f"[tradexyz-session] coins={len(coins)} window={start.date()}→{end.date()}", flush=True)
    for index, coin in enumerate(coins, 1):
        try:
            candles = info(
                {
                    "type": "candleSnapshot",
                    "req": {
                        "coin": coin,
                        "interval": "1h",
                        "startTime": start_ms,
                        "endTime": end_ms,
                    },
                }
            )
        except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
            print(f"  skip {coin}: {exc}", flush=True)
            continue
        can_close = underlying_can_close(coin)
        if isinstance(candles, list):
            for candle in candles:
                if not isinstance(candle, dict):
                    continue
                ts = int(candle.get("t") or 0)
                if ts < start_ms or ts >= end_ms:
                    continue
                usd = num(candle.get("v")) * num(candle.get("c"))
                if usd <= 0:
                    continue
                key = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")
                bucket = hours.setdefault(
                    key, {"total": 0.0, "outside_nyse": 0.0, "closed_underlying": 0.0}
                )
                bucket["total"] += usd
                rth = in_nyse_rth(ts)
                if not rth:
                    bucket["outside_nyse"] += usd
                    if can_close:
                        bucket["closed_underlying"] += usd
        if index % 20 == 0 or index == len(coins):
            print(f"  candles {index}/{len(coins)}", flush=True)
        time.sleep(0.12)
    save_cache(hours)
    return hours


def summarize(hours: dict[str, dict[str, float]], start: datetime, end: datetime) -> dict[str, Any]:
    start_key = start.strftime("%Y-%m-%dT%H")
    end_key = end.strftime("%Y-%m-%dT%H")
    total = outside = closed = 0.0
    for key, row in hours.items():
        if key < start_key or key >= end_key:
            continue
        total += num(row.get("total"))
        outside += num(row.get("outside_nyse"))
        closed += num(row.get("closed_underlying"))
    if total <= 0:
        raise RuntimeError("窗口成交为空")
    return {
        "window_start": start.date().isoformat(),
        "window_end": (end - timedelta(days=1)).date().isoformat(),
        "days": WINDOW_DAYS,
        "total_usd": round(total, 2),
        "outside_nyse_usd": round(outside, 2),
        "closed_underlying_usd": round(closed, 2),
        "outside_nyse_pct": round(outside / total * 100, 1),
        "closed_underlying_pct": round(closed / total * 100, 1),
    }


def yi(usd: float) -> str:
    if abs(usd) >= 1e8:
        return f"{usd / 1e8:.2f} 亿美元"
    return f"{usd / 1e4:.0f} 万美元"


def build_news(stats: dict[str, Any], oi: float) -> str:
    end = str(stats["window_end"])
    y, m, d = end.split("-")
    oi_bit = f"另据同一官方盘口，TradeXYZ 当前未平仓约 {yi(oi)}。" if oi > 0 else ""
    return (
        f"吴说获悉，据吴说数据中心统计，截至 {y} 年 {int(m)} 月 {int(d)} 日，"
        f"近 {WINDOW_DAYS} 个完整日 Hyperliquid HIP-3 提供商 TradeXYZ 永续成交中，"
        f"约 {stats['outside_nyse_pct']}% 发生在纽交所常规交易时段之外；"
        f"按美股及同类现金股市标的口径，约 {stats['closed_underlying_pct']}% 发生在标的常规交易时段闭市期间。"
        f"窗口合计成交约 {yi(stats['total_usd'])}。{oi_bit}"
        f"口径为 Hyperliquid 官方 1 小时 K 线成交额估算（dex=xyz），"
        f"纽交所时段取美东 09:30–16:00；商品/外汇/主要指数期货不计闭市；"
        f"不含各国节假日精细日历；非 Artemis/Blockworks。"
        f"\n{PAGE}"
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
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    end = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    start = end - timedelta(days=WINDOW_DAYS)
    hours = fetch_hours(start, end)
    stats = summarize(hours, start, end)
    oi = 0.0
    try:
        oi = xyz_oi_usd()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] oi {exc}", flush=True)

    # 窗口合计是截面标量，不要写成起止同值折线（会显示成横线、最后一条未平仓也像空图）
    inside_pct = round(max(0.0, 100.0 - float(stats["outside_nyse_pct"])), 1)
    payload = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "hyperliquid candleSnapshot 1h dex=xyz",
        "note": (
            "近90完整日。outside=非纽交所RTH；closed=美股/ETF等可闭市标的且非RTH。"
            "商品外汇指数期货不计闭市。非 Artemis。含当前 OI 快照。"
            "展示用卡片+柱状，不写假时间序列。"
        ),
        "note_en": (
            "Trailing 90 complete days. outside=non-NYSE RTH; closed=cash-equity-like "
            "underlyings outside RTH. Commodities/FX/major index futures not treated as closed. "
            "Not Artemis. Includes current OI snapshot. Cards/bars only; no fake time series."
        ),
        "latest": {**stats, "oi_usd": round(oi, 2)},
        "top": [
            {
                "name": "纽交所时段外成交",
                "nameEn": "Outside NYSE RTH volume",
                "amount": stats["outside_nyse_usd"],
            },
            {
                "name": "标的闭市成交（非加密）",
                "nameEn": "Closed underlying volume (non-crypto)",
                "amount": stats["closed_underlying_usd"],
            },
            {
                "name": "窗口合计成交",
                "nameEn": "Window total volume",
                "amount": stats["total_usd"],
            },
            {
                "name": "当前未平仓",
                "nameEn": "Current open interest",
                "amount": round(oi, 2),
            },
        ],
        "returns": [
            {
                "name": "纽交所时段外占比",
                "nameEn": "Outside NYSE RTH share",
                "return_pct": float(stats["outside_nyse_pct"]),
            },
            {
                "name": "标的闭市占比",
                "nameEn": "Closed underlying share",
                "return_pct": float(stats["closed_underlying_pct"]),
            },
            {
                "name": "纽交所时段内占比",
                "nameEn": "During NYSE RTH share",
                "return_pct": inside_pct,
            },
        ],
        "series": [],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(OUT_PATH)
    for mirror in (
        Path("/root/dashboard/public/json/hyperliquid/tradexyz_session.json"),
        ROOT.parent / "data-dashboard" / "public" / "json" / "hyperliquid" / "tradexyz_session.json",
    ):
        try:
            mirror.parent.mkdir(parents=True, exist_ok=True)
            mtmp = mirror.with_suffix(".json.tmp")
            mtmp.write_text(text, encoding="utf-8")
            mtmp.replace(mirror)
        except OSError:
            continue
    print(
        f"[tradexyz-session] end={stats['window_end']} outside={stats['outside_nyse_pct']}% "
        f"closed={stats['closed_underlying_pct']}% oi={oi:.0f} → {OUT_PATH}"
    )

    msg_id = f"tradexyz-session-{stats['window_end']}"
    text = as_news(build_news(stats, oi))
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
    try:
        send_lark(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Lark 失败: {exc}", file=sys.stderr)
        return 1
    print(f"[pushed] {msg_id}")
    if msg_id not in state["sentIds"]:
        state["sentIds"].append(msg_id)
    state["sentIds"] = state["sentIds"][-120:]
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
