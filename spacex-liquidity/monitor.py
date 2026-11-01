#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SpaceX 盘前永续：币安与 Hyperliquid 的成交和 3% 深度 → Lark 新闻。

口径固定，便于以后对上自己的数：
- 成交：过去 9 个已收盘 UTC 日。币安用 K 线成交额。Hyperliquid 用官方日 K 的张数乘收盘价。
- 深度：中间价上下 3% 内，买盘和卖盘名义金额合计。中间价 = 最优买价和最优卖价的平均。
- 币安用公开 depth 1000 档，必须盖住 3% 才计数。
- Hyperliquid 用公开订单簿 nSigFigs=3，必须盖住 3% 才计数。
- 同一天只发最新一条。深度在攒够小时样本之前只写「最新一次」，不写成多日平均。

用法：
  python monitor.py
  python monitor.py --push
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
OUT_PATH = HERE / "output" / "json" / "spacex_liquidity.json"
STATE_PATH = HERE / "output" / "lark_state.json"
LOG_PATH = HERE / "monitor.log"
LOCAL_MIRROR = ROOT.parent / "data-dashboard" / "public" / "json" / "spacex-liquidity" / "spacex_liquidity.json"
JSON_URL = "https://data.wublock123.com/dashboard/tradfi"
PAGE = "https://data.wublock123.com/dashboard/tradfi"
INFO_URL = "https://api.hyperliquid.xyz/info"
BN_DEPTH = "https://fapi.binance.com/fapi/v1/depth?symbol=SPCXUSDT&limit=1000"
BN_KLINES = "https://fapi.binance.com/fapi/v1/klines?symbol=SPCXUSDT&interval=1d&limit=15"
BN_FILES = [
    Path("/root/dashboard/public/json/tradfi-price/Binance_SPCXUSDT_1d.json"),
    ROOT.parent / "data-dashboard" / "public" / "json" / "tradfi-price" / "Binance_SPCXUSDT_1d.json",
]
UA = "Mozilla/5.0"
TZ_CN = timezone(timedelta(hours=8))
BAND = 0.03
VOLUME_DAYS = 9
KEEP_HOURS = 24 * 14
HOUR_AVG_MIN = 18
NINE_DAY_AVG_MIN = 9 * 18
ENV_CANDIDATES = [
    Path("/root/dashboard/.env.live"),
    ROOT.parent / "data-dashboard" / ".env.live",
    ROOT / ".env",
]

sys.path.insert(0, str(ROOT))
from lark_copy import as_news  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


class BinanceBan(RuntimeError):
    def __init__(self, until_ms: int):
        super().__init__(f"币安限流，解禁时间 {until_ms}")
        self.until_ms = until_ms


def http_json(url: str, body: dict[str, Any] | None = None) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        if exc.code == 418:
            until = 0
            try:
                msg = str(json.loads(raw).get("msg") or "")
            except json.JSONDecodeError:
                msg = raw
            marker = "banned until "
            if marker in msg:
                tail = msg.split(marker, 1)[1].split(".", 1)[0].strip()
                until = int(tail) if tail.isdigit() else 0
            if until <= 0:
                until = int(time.time() * 1000) + 60 * 60 * 1000
            raise BinanceBan(until) from exc
        raise RuntimeError(f"请求失败 {url.split('?')[0]}: {exc.code} {raw[:180]}") from exc


def pairs_from(rows: list[Any]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for row in rows:
        if isinstance(row, dict):
            price = float(row.get("px") or 0)
            size = float(row.get("sz") or 0)
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            price = float(row[0])
            size = float(row[1])
        else:
            continue
        if price > 0 and size > 0:
            out.append((price, size))
    return out


def band_depth(bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> dict[str, Any] | None:
    if not bids or not asks:
        return None
    best_bid = max(price for price, _ in bids)
    best_ask = min(price for price, _ in asks)
    if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
        return None
    mid = (best_bid + best_ask) / 2
    lo = mid * (1 - BAND)
    hi = mid * (1 + BAND)
    if min(price for price, _ in bids) > lo or max(price for price, _ in asks) < hi:
        return None
    bid_notional = sum(price * size for price, size in bids if price >= lo)
    ask_notional = sum(price * size for price, size in asks if price <= hi)
    return {
        "mid": round(mid, 4),
        "bid_usd": round(bid_notional, 2),
        "ask_usd": round(ask_notional, 2),
        "sum_usd": round(bid_notional + ask_notional, 2),
    }


def hl_depth() -> dict[str, Any]:
    tight = http_json(INFO_URL, {"type": "l2Book", "coin": "xyz:SPCX"})
    wide = http_json(INFO_URL, {"type": "l2Book", "coin": "xyz:SPCX", "nSigFigs": 3})
    if not isinstance(tight, dict) or not isinstance(wide, dict):
        raise RuntimeError("Hyperliquid 订单簿返回异常")
    tight_levels = tight.get("levels") or []
    wide_levels = wide.get("levels") or []
    if len(tight_levels) < 2 or len(wide_levels) < 2:
        raise RuntimeError("Hyperliquid 订单簿缺档")
    best_bids = pairs_from(tight_levels[0])
    best_asks = pairs_from(tight_levels[1])
    if not best_bids or not best_asks:
        raise RuntimeError("Hyperliquid 没有最优价")
    mid = (max(price for price, _ in best_bids) + min(price for price, _ in best_asks)) / 2
    bids = pairs_from(wide_levels[0])
    asks = pairs_from(wide_levels[1])
    lo = mid * (1 - BAND)
    hi = mid * (1 + BAND)
    if not bids or not asks or min(price for price, _ in bids) > lo or max(price for price, _ in asks) < hi:
        raise RuntimeError("Hyperliquid 订单簿没有盖住中间价 3%")
    bid_notional = sum(price * size for price, size in bids if price >= lo)
    ask_notional = sum(price * size for price, size in asks if price <= hi)
    return {
        "mid": round(mid, 4),
        "bid_usd": round(bid_notional, 2),
        "ask_usd": round(ask_notional, 2),
        "sum_usd": round(bid_notional + ask_notional, 2),
    }


def bn_depth() -> dict[str, Any]:
    book = http_json(BN_DEPTH)
    if not isinstance(book, dict):
        raise RuntimeError("币安深度返回异常")
    depth = band_depth(pairs_from(book.get("bids") or []), pairs_from(book.get("asks") or []))
    if depth is None:
        raise RuntimeError("币安 1000 档没有盖住中间价 3%")
    return depth


def utc_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def binance_volume_from_file(now_ms: int) -> dict[str, float]:
    path = next((item for item in BN_FILES if item.is_file()), None)
    if path is None:
        raise RuntimeError("币安日 K 接口不可用，本地也没有 tradfi-price 文件")
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError(f"币安成交文件格式不对: {path}")
    out: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        open_ms = int(row.get("time") or 0)
        if open_ms <= 0 or open_ms + 86_400_000 > now_ms:
            continue
        volume = float(row.get("volume_usd") or 0)
        if volume <= 0:
            continue
        out[utc_day(open_ms)] = volume
    if not out:
        raise RuntimeError(f"币安成交文件没有已收盘的日子: {path}")
    print(f"[spacex-liquidity] 币安成交改用 {path}")
    return out


def binance_volume(now_ms: int) -> dict[str, float]:
    # 网页日 K 已经每天落地。优先用它，避免再打币安 K 线把服务器打进限流。
    if any(path.is_file() for path in BN_FILES):
        try:
            return binance_volume_from_file(now_ms)
        except (OSError, json.JSONDecodeError, RuntimeError, ValueError) as exc:
            print(f"[warn] 读币安成交文件失败，改走接口: {exc}")
    try:
        klines = http_json(BN_KLINES)
    except BinanceBan:
        raise
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError, ValueError) as exc:
        print(f"[warn] 币安日 K 接口不可用: {exc}")
        return binance_volume_from_file(now_ms)
    if not isinstance(klines, list):
        return binance_volume_from_file(now_ms)
    out: dict[str, float] = {}
    for bar in klines:
        if not isinstance(bar, list) or len(bar) < 8:
            continue
        if int(bar[6]) >= now_ms:
            continue
        out[utc_day(int(bar[0]))] = float(bar[7])
    if len(out) < VOLUME_DAYS:
        return binance_volume_from_file(now_ms)
    return out


def volume_9d() -> list[dict[str, Any]]:
    now_ms = int(time.time() * 1000)
    bn = binance_volume(now_ms)
    start = now_ms - 16 * 86400000
    candles = http_json(INFO_URL, {
        "type": "candleSnapshot",
        "req": {"coin": "xyz:SPCX", "interval": "1d", "startTime": start, "endTime": now_ms},
    })
    if not isinstance(candles, list):
        raise RuntimeError("Hyperliquid 日 K 返回异常")
    hl: dict[str, float] = {}
    for bar in candles:
        if not isinstance(bar, dict):
            continue
        close_ms = int(bar.get("T") or 0)
        if close_ms >= now_ms:
            continue
        base = float(bar.get("v") or 0)
        close = float(bar.get("c") or 0)
        if base <= 0 or close <= 0:
            continue
        hl[utc_day(int(bar["t"]))] = base * close
    days = sorted(set(bn) & set(hl))[-VOLUME_DAYS:]
    if len(days) < VOLUME_DAYS:
        raise RuntimeError(f"完整交易日只有 {len(days)} 天")
    latest = datetime.strptime(days[-1], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - latest > timedelta(days=3):
        raise RuntimeError(f"最近一个完整交易日是 {days[-1]}，成交太旧")
    return [{"day": day, "binance_usd": round(bn[day], 2), "hyperliquid_usd": round(hl[day], 2)} for day in days]


def usd(value: float) -> str:
    if value >= 1e7:
        return f"{value / 1e8:.2f} 亿美元"
    if value >= 1e4:
        return f"{value / 1e4:.2f} 万美元"
    return f"{value:.0f} 美元"


def load_prior() -> dict[str, Any]:
    if not OUT_PATH.is_file():
        return {"samples": []}
    try:
        doc = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"samples": []}
    samples = doc.get("samples") if isinstance(doc, dict) else None
    if not isinstance(samples, list):
        return {"samples": []}
    return {"samples": [row for row in samples if isinstance(row, dict) and row.get("hour")]}


def write_json(payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    paths = [OUT_PATH]
    if LOCAL_MIRROR.parents[2].is_dir():
        paths.append(LOCAL_MIRROR)
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"sentIds": []}
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sentIds": []}
    ids = doc.get("sentIds") if isinstance(doc, dict) else None
    ban_until = doc.get("banUntilMs") if isinstance(doc, dict) else 0
    return {
        "sentIds": [item for item in ids if isinstance(item, str)] if isinstance(ids, list) else [],
        "banUntilMs": int(ban_until) if isinstance(ban_until, (int, float)) else 0,
    }


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = now_iso()
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def gen_lark_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_lark_text(webhook: str, secret: str | None, text: str) -> None:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": as_news(text)}}
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
        body = resp.read().decode("utf-8", errors="replace")
    if body:
        result = json.loads(body)
        if str(result.get("code", "0")) != "0":
            raise RuntimeError(f"Lark 返回异常: {result}")


def append_log(stamp: str, msg_id: str, ok: bool, text: str) -> None:
    line = json.dumps({"ts": stamp, "id": msg_id, "ok": ok, "text": text}, ensure_ascii=False)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def average_of(samples: list[dict[str, Any]], hours: int, minimum: int) -> dict[str, Any] | None:
    if not samples:
        return None
    latest = max(str(row["hour"]) for row in samples)
    latest_dt = datetime.strptime(latest, "%Y-%m-%dT%H").replace(tzinfo=TZ_CN)
    cutoff = (latest_dt - timedelta(hours=hours - 1)).strftime("%Y-%m-%dT%H")
    chosen = [row for row in samples if str(row["hour"]) >= cutoff]
    if len(chosen) < minimum:
        return None
    return {
        "n": len(chosen),
        "binance_usd": round(sum(float(row["binance_usd"]) for row in chosen) / len(chosen), 2),
        "hyperliquid_usd": round(sum(float(row["hyperliquid_usd"]) for row in chosen) / len(chosen), 2),
    }


def news_text(days: list[dict[str, Any]], live: dict[str, Any], samples: list[dict[str, Any]]) -> str:
    bn_sum = sum(float(row["binance_usd"]) for row in days)
    hl_sum = sum(float(row["hyperliquid_usd"]) for row in days)
    ratio = bn_sum / hl_sum
    measured = datetime.strptime(str(live.get("asof") or now_iso()), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(TZ_CN)
    stamp = f"{measured.year} 年 {measured.month} 月 {measured.day} 日 {measured.hour:02d}:{measured.minute:02d}"
    bn_depth = float(live["binance"]["sum_usd"])
    hl_depth = float(live["hyperliquid"]["sum_usd"])
    thicker = "Hyperliquid 的" if hl_depth > bn_depth else "币安的"
    lines = [
        (
            f"据吴说数据中心统计，截至 {stamp}（UTC+8），SpaceX 盘前永续过去 9 个完整交易日，"
            f"币安日均成交约 {usd(bn_sum / len(days))}，Hyperliquid 日均成交约 {usd(hl_sum / len(days))}，"
            f"币安约为 Hyperliquid 的 {ratio:.1f} 倍。"
            f"中间价上下 3% 的订单簿深度，最新一次 Hyperliquid 买卖合计约 {usd(hl_depth)}，"
            f"币安约 {usd(bn_depth)}。按最新这一次，{thicker}挂单更厚。"
        )
    ]
    day_avg = average_of(samples, 24, HOUR_AVG_MIN)
    if day_avg:
        lines.append(
            f"过去 24 小时按小时采样 {day_avg['n']} 次，Hyperliquid 平均深度约 {usd(day_avg['hyperliquid_usd'])}，"
            f"币安平均约 {usd(day_avg['binance_usd'])}。"
        )
    nine = average_of(samples, 24 * 9, NINE_DAY_AVG_MIN)
    if nine:
        lines.append(
            f"过去 9 天按小时采样 {nine['n']} 次，Hyperliquid 平均深度约 {usd(nine['hyperliquid_usd'])}，"
            f"币安平均约 {usd(nine['binance_usd'])}。"
        )
    lines.append(JSON_URL)
    return "\n".join(lines)


def load_saved() -> dict[str, Any] | None:
    if not OUT_PATH.is_file():
        return None
    try:
        doc = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(doc, dict):
        return None
    live = doc.get("live") if isinstance(doc.get("live"), dict) else None
    days = doc.get("volume_9d") if isinstance(doc.get("volume_9d"), list) else None
    samples = doc.get("samples") if isinstance(doc.get("samples"), list) else None
    if not live or not isinstance(live.get("binance"), dict) or not isinstance(live.get("hyperliquid"), dict):
        return None
    if not days or not samples:
        return None
    return doc


def publish(state: dict[str, Any], payload: dict[str, Any], push: bool) -> int:
    days = payload["volume_9d"]
    samples = payload["samples"]
    text = news_text(days, payload["live"], samples)
    day = datetime.now(TZ_CN).strftime("%Y-%m-%d")
    msg_id = f"spacex-liquidity-{day}"
    bn = payload["live"]["binance"]
    hl = payload["live"]["hyperliquid"]
    print(
        f"[spacex-liquidity] bn={float(bn['sum_usd']):.0f} hl={float(hl['sum_usd']):.0f} "
        f"vol_days={days[0]['day']}..{days[-1]['day']} samples={len(samples)}"
    )
    print("---")
    print(f"id={msg_id} already={msg_id in state['sentIds']}")
    print(text)
    if msg_id in state["sentIds"]:
        print("[skip] 今天这条已经发过")
        save_state(state)
        return 0
    if not push:
        print("[dry-run] 未推送")
        save_state(state)
        return 0
    env = load_env()
    webhook = (env.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (env.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 2
    stamp = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    try:
        send_lark_text(webhook, secret, text)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Lark 失败 {msg_id}: {exc}", file=sys.stderr)
        append_log(stamp, msg_id, False, text)
        return 1
    print(f"[pushed] {msg_id}")
    state["sentIds"] = (state["sentIds"] + [msg_id])[-200:]
    save_state(state)
    append_log(stamp, msg_id, True, text)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--use-saved", action="store_true")
    args = parser.parse_args()
    state = load_state()
    hour = datetime.now(TZ_CN).strftime("%Y-%m-%dT%H")
    now_ms = int(time.time() * 1000)
    banned = int(state.get("banUntilMs") or 0) > now_ms
    saved = load_saved()
    saved_hour = ""
    if saved:
        hours = [str(row.get("hour") or "") for row in saved["samples"] if isinstance(row, dict)]
        saved_hour = max(hours) if hours else ""

    if args.use_saved:
        if saved is None:
            print("[ERROR] 没有可发送的快照", file=sys.stderr)
            return 1
        return publish(state, saved, args.push)

    if saved is not None and saved_hour == hour and f"spacex-liquidity-{hour[:10]}" in state["sentIds"]:
        print("[skip] 这一小时已采样，今天已发")
        return 0
    if banned and not (saved is not None and saved_hour == hour):
        until = datetime.fromtimestamp(int(state["banUntilMs"]) / 1000, TZ_CN).strftime("%H:%M")
        print(f"[skip] 币安限流未解除，{until} 后再采样")
        return 0
    if saved is not None and saved_hour == hour:
        return publish(state, saved, args.push)

    try:
        bn = bn_depth()
        hl = hl_depth()
        days = volume_9d()
    except BinanceBan as exc:
        state["banUntilMs"] = exc.until_ms
        save_state(state)
        until = datetime.fromtimestamp(exc.until_ms / 1000, TZ_CN).strftime("%Y-%m-%d %H:%M")
        print(f"[skip] 币安限流，{until} 后再试")
        return 0
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    prior = load_prior()
    samples = [row for row in prior["samples"] if str(row.get("hour")) != hour]
    samples.append({
        "hour": hour,
        "binance_usd": bn["sum_usd"],
        "hyperliquid_usd": hl["sum_usd"],
    })
    samples = samples[-KEEP_HOURS:]
    payload = {
        "updated_at": now_iso(),
        "source": "Binance fapi SPCXUSDT depth；网页 tradfi-price 日成交；Hyperliquid xyz:SPCX l2Book+candleSnapshot",
        "note": (
            "深度是中间价上下 3% 的买卖名义金额合计。币安须 1000 档盖住 3%。"
            "Hyperliquid 用 nSigFigs=3，须盖住 3%。"
            "成交是过去 9 个已收盘 UTC 日：币安用网页已落地的成交额；Hyperliquid 为张数乘收盘价。"
            "每小时采样一次。未攒够样本时，新闻只写最新一次深度。"
        ),
        "live": {"asof": now_iso(), "binance": bn, "hyperliquid": hl},
        "volume_9d": days,
        "samples": samples,
    }
    write_json(payload)
    return publish(state, payload, args.push)


if __name__ == "__main__":
    raise SystemExit(main())
