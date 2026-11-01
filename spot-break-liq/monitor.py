"""OKX 现货关口突破/跌破 + CoinGlass 全球 24h 爆仓 → Lark【历史阈值警报】。

- 跌破：价格从上方向下穿过关口；回到关口上方后可再次触发。
- 突破：价格从下方向上穿过关口；回到关口下方后可再次触发。
- 同方向、同关口、同上海日只发一次。
- 爆仓：用 exchange-list 的 All 行（全球合计+多空）；≥2 亿 notable、≥5 亿 major；
  同一上海日同一档只发一次，可从 notable 升到 major。
- 不写爆仓人数（公开 API 无此字段）。不占 Dune。

用法：
  python monitor.py              # 只打印候选
  python monitor.py --push       # 有候选才发
  python monitor.py --push --force
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
STATE_PATH = HERE / "state.json"
LOG_PATH = HERE / "monitor.log"
OUT_JSON = HERE / "output" / "json" / "latest.json"
DASHBOARD_JSON = Path("/root/dashboard/public/json/spot-break-liq/latest.json")
TZ_CN = timezone(timedelta(hours=8))
PAGE = "https://data.wublock123.com/dashboard/lark-feeds/spot-break-liq"
HISTORY_DAYS = 120

OKX_TICKERS = "https://www.okx.com/api/v5/market/tickers?instType=SPOT"
OKX_CANDLES = "https://www.okx.com/api/v5/market/candles"
CG_LIQ_URL = "https://open-api-v4.coinglass.com/api/futures/liquidation/exchange-list?range=24h"
CG_COIN_LIQ_URL = "https://open-api-v4.coinglass.com/api/futures/liquidation/coin-list?range=24h"
PAGE_HOME = PAGE

# Round levels for break alerts (USD).
LEVELS: dict[str, list[float]] = {
    "BTC-USDT": [100000, 95000, 90000, 85000, 80000, 75000, 70000, 65000, 60000],
    "ETH-USDT": [4000, 3500, 3000, 2800, 2500, 2400, 2300, 2000, 1800, 1700, 1600, 1550, 1500],
    "SOL-USDT": [200, 150, 120, 110, 100, 90, 80, 70, 60],
}
LABELS = {"BTC-USDT": "比特币", "ETH-USDT": "以太坊", "SOL-USDT": "SOL"}
BASE_UNITS = {"BTC-USDT": "BTC", "ETH-USDT": "ETH", "SOL-USDT": "SOL"}

LIQ_NOTABLE = float(os.environ.get("LIQ_24H_NOTABLE_USD") or 2e8)
LIQ_MAJOR = float(os.environ.get("LIQ_24H_MAJOR_USD") or 5e8)

ENV_FILES = [
    HERE / ".env",
    HERE.parent / ".env",
    Path("/root/dashboard/.env.live"),
    HERE.parent.parent / "data-dashboard" / ".env.live",
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
    merged: dict[str, str] = {}
    for path in ENV_FILES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            merged[k.strip()] = v.strip().strip('"').strip("'")
    for key in ("LARK_ALERT_WEBHOOK", "LARK_ALERT_SECRET", "COINGLASS_API_KEY"):
        if os.environ.get(key):
            merged[key] = os.environ[key]
    if not merged.get("COINGLASS_API_KEY"):
        common = HERE.parent / "coinglass-history" / "_common.py"
        if common.is_file():
            for line in common.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("COINGLASS_API_KEY"):
                    merged["COINGLASS_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
    return merged


def http_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "wu-spot-break-liq/1.0", "Accept": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_state() -> dict[str, Any]:
    empty = {
        "prices": {},
        "break_sent": {},
        "break_armed": {},
        "breakup_sent": {},
        "breakup_armed": {},
        "liq_sent": {},
        "updated_at": "",
    }
    if not STATE_PATH.is_file():
        return dict(empty)
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(empty)
    if not isinstance(raw, dict):
        return dict(empty)
    for key in empty:
        raw.setdefault(key, empty[key] if key != "updated_at" else "")
    return raw


def save_state(state: dict[str, Any]) -> None:
    state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def _load_prior_latest() -> dict[str, Any]:
    for path in (OUT_JSON, DASHBOARD_JSON):
        if not path.is_file():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(doc, dict):
            return doc
    return {}


def fetch_okx_daily_closes(inst: str, limit: int = HISTORY_DAYS) -> list[dict[str, Any]]:
    """OKX 日 K 收盘，旧→新。"""
    url = f"{OKX_CANDLES}?instId={inst}&bar=1D&limit={min(limit, 300)}"
    try:
        payload = http_json(url)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError, ValueError):
        return []
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            ts_ms = int(row[0])
            close = float(row[4])
        except (TypeError, ValueError):
            continue
        day = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        out.append({"month": day, "usd": close})
    out.sort(key=lambda item: item["month"])
    return out


def write_latest_json(
    prices: dict[str, dict[str, float]],
    liq: dict[str, float],
    liq_coins: dict[str, dict[str, float]],
    now: datetime,
    latest_event: dict[str, Any] | None = None,
) -> Path:
    """落盘看板可读 JSON：现货三币 + 爆仓柱 + 日收盘序列（供 lark-feeds 画图）。"""
    prior = _load_prior_latest()
    day = now.strftime("%Y-%m-%d")
    asof = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    prior_series = prior.get("series") if isinstance(prior.get("series"), list) else []
    by_label: dict[str, list[dict[str, Any]]] = {}
    for block in prior_series:
        if not isinstance(block, dict):
            continue
        label = str(block.get("label") or "")
        months = block.get("months")
        if not label or not isinstance(months, list):
            continue
        kept: list[dict[str, Any]] = []
        for row in months:
            if not isinstance(row, dict):
                continue
            m = str(row.get("month") or "")
            usd = row.get("usd")
            if m and isinstance(usd, (int, float)):
                kept.append({"month": m, "usd": float(usd)})
        by_label[label] = kept

    label_map = [
        ("BTC-USDT", "比特币", "BTC"),
        ("ETH-USDT", "以太坊", "ETH"),
        ("SOL-USDT", "SOL", "SOL"),
    ]
    series_out: list[dict[str, Any]] = []
    for inst, label_zh, label_en in label_map:
        rows = [r for r in by_label.get(label_zh, []) if r["month"] != day]
        if len(rows) < 2:
            seeded = fetch_okx_daily_closes(inst, HISTORY_DAYS)
            if seeded:
                rows = [r for r in seeded if r["month"] != day]
        rows.append({"month": day, "usd": round(float(prices[inst]["last"]), 6)})
        # de-dupe by day, keep last
        merged: dict[str, float] = {}
        for row in rows:
            merged[row["month"]] = float(row["usd"])
        rows = [{"month": k, "usd": round(v, 6)} for k, v in sorted(merged.items())][-HISTORY_DAYS:]
        series_out.append({"label": label_zh, "labelEn": label_en, "months": rows})

    primary_inst = "BTC-USDT"
    if latest_event and latest_event.get("inst") in prices:
        primary_inst = str(latest_event["inst"])
    elif isinstance(prior.get("primary_inst"), str) and prior["primary_inst"] in prices:
        # keep last break focus for a bit when no new event this tick
        if prior.get("latest_event"):
            primary_inst = str(prior["primary_inst"])

    primary = prices[primary_inst]
    assets = []
    for inst, label_zh, label_en in label_map:
        px = prices[inst]
        coin = liq_coins.get(inst) or {}
        assets.append(
            {
                "inst": inst,
                "symbol": label_en,
                "name": label_zh,
                "price": round(px["last"], 6),
                "chg_24h_pct": round(px["chg24"], 4),
                "high_24h": round(px.get("high24") or 0, 6) or None,
                "low_24h": round(px.get("low24") or 0, 6) or None,
                "vol_ccy_24h": round(px.get("vol_ccy24") or 0, 2),
                "liq_24h_usd": round(float(coin.get("total") or 0), 2) or None,
            }
        )

    event_out = None
    if latest_event:
        event_out = {
            "id": latest_event.get("id"),
            "kind": latest_event.get("kind"),
            "direction": latest_event.get("direction"),
            "inst": latest_event.get("inst"),
            "level": latest_event.get("level"),
            "price": latest_event.get("price"),
            "asof": asof,
            "day": day,
        }
    elif isinstance(prior.get("latest_event"), dict):
        event_out = prior["latest_event"]

    payload = {
        "updated_at": asof,
        "source": "OKX spot tickers + CoinGlass liquidation exchange-list/coin-list",
        "name": "spot-break-liq",
        "primary_inst": primary_inst,
        "live": {
            "asof": asof,
            "okx_price": round(primary["last"], 6),
            "price": round(primary["last"], 6),
            "chg_24h_pct": round(primary["chg24"], 4),
            "high_24h": round(primary.get("high24") or 0, 6) or None,
            "low_24h": round(primary.get("low24") or 0, 6) or None,
            "symbol": LABELS.get(primary_inst, primary_inst),
        },
        "assets": assets,
        "liq_24h": {
            "total": round(liq["total"], 2),
            "long": round(liq["long"], 2),
            "short": round(liq["short"], 2),
        },
        "top": [
            {"name": "全球多单爆仓", "fees_usd": round(liq["long"], 2)},
            {"name": "全球空单爆仓", "fees_usd": round(liq["short"], 2)},
        ],
        "series": series_out,
        "latest_event": event_out,
        "page": PAGE,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT_JSON)
    return OUT_JSON


def fmt_usd(value: float) -> str:
    if value >= 1e8:
        return f"{value / 1e8:.2f} 亿美元"
    if value >= 1e4:
        return f"{value / 1e4:,.0f} 万美元"
    return f"{value:,.0f} 美元"


def fmt_price(value: float) -> str:
    if value >= 1000:
        return f"{value:,.2f}".rstrip("0").rstrip(".") + " 美元"
    return f"{value:,.2f} 美元"


def fmt_level(value: float) -> str:
    if value >= 1000:
        return f"{value:,.0f} 美元"
    return f"{value:g} 美元"


def fmt_chg(pct: float) -> str:
    return f"下跌 {abs(pct):.2f}%" if pct < 0 else f"上涨 {pct:.2f}%"


def fmt_chg_usd(delta: float) -> str:
    if delta >= 0:
        return f"上涨 {delta:,.2f} 美元"
    return f"下跌 {abs(delta):,.2f} 美元"


def fmt_base_vol(value: float, unit: str) -> str:
    if value >= 1000:
        return f"{value:,.2f} {unit}"
    return f"{value:.2f} {unit}"


def fetch_okx() -> dict[str, dict[str, float]]:
    payload = http_json(OKX_TICKERS)
    if str(payload.get("code")) != "0":
        raise RuntimeError(f"OKX tickers failed: {payload.get('msg')}")
    out: dict[str, dict[str, float]] = {}
    for row in payload.get("data") or []:
        inst = str(row.get("instId") or "")
        if inst not in LEVELS:
            continue
        last = float(row["last"])
        open24 = float(row.get("open24h") or 0)
        chg = ((last - open24) / open24 * 100.0) if open24 > 0 else 0.0
        chg_usd = (last - open24) if open24 > 0 else 0.0
        out[inst] = {
            "last": last,
            "open24": open24,
            "chg24": chg,
            "chg_usd": chg_usd,
            "high24": float(row.get("high24h") or 0),
            "low24": float(row.get("low24h") or 0),
            "vol24": float(row.get("vol24h") or 0),
            "vol_ccy24": float(row.get("volCcy24h") or 0),
        }
    if len(out) < 3:
        raise RuntimeError(f"OKX missing symbols: {sorted(LEVELS)} got {sorted(out)}")
    return out


def fetch_liq_all(api_key: str) -> dict[str, float]:
    payload = http_json(CG_LIQ_URL, headers={"CG-API-KEY": api_key})
    if str(payload.get("code")) not in ("0", "0.0") and payload.get("code") != 0:
        raise RuntimeError(f"CoinGlass liq failed: {payload.get('msg')}")
    rows = payload.get("data") or []
    for row in rows:
        if str(row.get("exchange") or "").lower() == "all":
            total = float(row.get("liquidation_usd") or 0)
            long_usd = float(row.get("longLiquidation_usd") or 0)
            short_usd = float(row.get("shortLiquidation_usd") or 0)
            if total <= 0:
                raise RuntimeError("All liquidation_usd empty")
            return {"total": total, "long": long_usd, "short": short_usd}
    raise RuntimeError("CoinGlass All row missing")


def _coin_liq_usd(row: dict[str, Any]) -> float:
    return float(
        row.get("liquidation_usd_24h")
        or row.get("liquidation_usd")
        or row.get("total_liquidation_usd")
        or 0
    )


def fetch_liq_coins(api_key: str) -> dict[str, dict[str, float]]:
    """BTC/ETH/SOL 24h 爆仓。失败返回空 dict，不挡关口稿。"""
    try:
        payload = http_json(CG_COIN_LIQ_URL, headers={"CG-API-KEY": api_key})
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"[warn] coin-list 失败: {exc}", file=sys.stderr)
        return {}
    if str(payload.get("code") or "0") not in ("0", "0.0") and payload.get("code") != 0:
        print(f"[warn] coin-list code={payload.get('code')}", file=sys.stderr)
        return {}
    wanted = {"BTC": "BTC-USDT", "ETH": "ETH-USDT", "SOL": "SOL-USDT"}
    out: dict[str, dict[str, float]] = {}
    for row in payload.get("data") or []:
        if not isinstance(row, dict):
            continue
        inst = wanted.get(str(row.get("symbol") or "").upper())
        if not inst:
            continue
        total = _coin_liq_usd(row)
        if total <= 0:
            continue
        out[inst] = {
            "total": total,
            "long": float(row.get("long_liquidation_usd_24h") or row.get("longLiquidation_usd") or 0),
            "short": float(row.get("short_liquidation_usd_24h") or row.get("shortLiquidation_usd") or 0),
        }
    return out


def liq_sentence(liq: dict[str, float]) -> str:
    side = "主爆多单" if liq["long"] >= liq["short"] else "主爆空单"
    return (
        f"约 {fmt_usd(liq['total'])}（{side}，多单约 {fmt_usd(liq['long'])}，"
        f"空单约 {fmt_usd(liq['short'])}）"
    )


def detect_breaks(
    prices: dict[str, dict[str, float]],
    state: dict[str, Any],
    now: datetime,
) -> list[dict[str, Any]]:
    """Down-cross: fire when prev >= level > cur; re-arm after recovering above."""
    prev_prices: dict[str, float] = {
        k: float(v) for k, v in (state.get("prices") or {}).items() if isinstance(v, (int, float, str))
    }
    armed: dict[str, bool] = {
        str(k): bool(v) for k, v in (state.get("break_armed") or {}).items()
    }
    sent: dict[str, str] = dict(state.get("break_sent") or {})
    day = now.strftime("%Y-%m-%d")
    events: list[dict[str, Any]] = []

    for inst, levels in LEVELS.items():
        cur = prices[inst]["last"]
        prev = prev_prices.get(inst)
        for level in levels:
            key = f"{inst}:{level:g}"
            if cur >= level:
                armed[key] = True
                continue
            if prev is None:
                armed.setdefault(key, False)
                continue
            if not armed.get(key, True):
                continue
            if prev >= level > cur:
                event_id = f"okx-break-{inst.split('-')[0].lower()}-{level:g}-{day}"
                if sent.get(key) == event_id:
                    armed[key] = False
                    continue
                events.append(
                    {
                        "id": event_id,
                        "kind": "break",
                        "direction": "down",
                        "inst": inst,
                        "level": level,
                        "price": cur,
                        "latch": key,
                    }
                )
                armed[key] = False
    state["break_armed"] = armed
    state["break_sent"] = sent
    return events


def detect_breakups(
    prices: dict[str, dict[str, float]],
    state: dict[str, Any],
    now: datetime,
) -> list[dict[str, Any]]:
    """Up-cross: fire when prev < level <= cur; re-arm after falling back below."""
    prev_prices: dict[str, float] = {
        k: float(v) for k, v in (state.get("prices") or {}).items() if isinstance(v, (int, float, str))
    }
    armed: dict[str, bool] = {
        str(k): bool(v) for k, v in (state.get("breakup_armed") or {}).items()
    }
    sent: dict[str, str] = dict(state.get("breakup_sent") or {})
    day = now.strftime("%Y-%m-%d")
    events: list[dict[str, Any]] = []

    for inst, levels in LEVELS.items():
        cur = prices[inst]["last"]
        prev = prev_prices.get(inst)
        for level in levels:
            key = f"{inst}:{level:g}"
            if cur < level:
                armed[key] = True
                continue
            if prev is None:
                # Already above on first tick: do not backfill a breakout.
                armed.setdefault(key, False)
                continue
            if not armed.get(key, True):
                continue
            if prev < level <= cur:
                event_id = f"okx-breakup-{inst.split('-')[0].lower()}-{level:g}-{day}"
                if sent.get(key) == event_id:
                    armed[key] = False
                    continue
                events.append(
                    {
                        "id": event_id,
                        "kind": "breakup",
                        "direction": "up",
                        "inst": inst,
                        "level": level,
                        "price": cur,
                        "latch": key,
                    }
                )
                armed[key] = False
    state["breakup_armed"] = armed
    state["breakup_sent"] = sent
    return events


def detect_liq(liq: dict[str, float], state: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    total = liq["total"]
    if total < LIQ_NOTABLE:
        return None
    severity = "major" if total >= LIQ_MAJOR else "notable"
    day = now.strftime("%Y-%m-%d")
    sent: dict[str, str] = dict(state.get("liq_sent") or {})
    if sent.get(f"{day}:{severity}"):
        return None
    if severity == "notable" and sent.get(f"{day}:major"):
        return None
    return {
        "id": f"cg-liq-24h-{severity}-{day}",
        "kind": "liq",
        "severity": severity,
        "total": total,
        "long": liq["long"],
        "short": liq["short"],
        "day_key": f"{day}:{severity}",
    }


def build_break_text(
    event: dict[str, Any],
    prices: dict[str, dict[str, float]],
    now: datetime,
    liq_all: dict[str, float] | None = None,
    liq_coins: dict[str, dict[str, float]] | None = None,
) -> str:
    inst = event["inst"]
    label = LABELS[inst]
    base = BASE_UNITS[inst]
    px = prices[inst]
    stamp = f"{now.month} 月 {now.day} 日 {now.hour:02d}:{now.minute:02d}"
    level_s = fmt_level(event["level"])
    up = event.get("direction") == "up" or event.get("kind") == "breakup"
    verb = "突破" if up else "跌破"
    chg_word = "涨幅" if px["chg24"] >= 0 else "跌幅"
    body = (
        f"据吴说数据中心统计，截至 {stamp}（UTC+8），{label}{verb} {level_s}，"
        f"现报 {fmt_price(event['price'])}，24 小时{fmt_chg_usd(px['chg_usd'])}，"
        f"{chg_word}达 {abs(px['chg24']):.2f}%。"
    )
    if px.get("high24") and px.get("low24"):
        body += (
            f"过去 24 小时最高触及 {fmt_price(px['high24'])}，"
            f"最低为 {fmt_price(px['low24'])}。"
        )
    if px.get("vol24") or px.get("vol_ccy24"):
        body += (
            f"{inst} 交易对 24 小时成交量约 {fmt_base_vol(px['vol24'], base)}，"
            f"成交额约 {fmt_usd(px['vol_ccy24'])}。"
        )
    coin_liq = (liq_coins or {}).get(inst)
    if coin_liq:
        body += f"近 24 小时{label}合约爆仓{liq_sentence(coin_liq)}。"
    if liq_all:
        body += f"同期全球爆仓{liq_sentence(liq_all)}。"

    lines = ["【历史阈值警报】", body]
    bits = []
    for other, other_label in LABELS.items():
        if other == inst:
            continue
        opx = prices[other]
        bits.append(f"{other_label}现报 {fmt_price(opx['last'])}，24 小时{fmt_chg(opx['chg24'])}")
    if bits:
        lines.append("；".join(bits) + "。")
    lines.append(PAGE_HOME)
    return "\n".join(lines)


def build_liq_text(event: dict[str, Any], prices: dict[str, dict[str, float]]) -> str:
    side = "主爆多单" if event["long"] >= event["short"] else "主爆空单"
    main = event["long"] if event["long"] >= event["short"] else event["short"]
    side_label = "多单" if event["long"] >= event["short"] else "空单"
    lines = [
        "【历史阈值警报】",
        f"据吴说数据中心统计，近 24 小时全球爆仓总金额约 {fmt_usd(event['total'])}，{side}，"
        f"{side_label}爆仓约 {fmt_usd(main)}；"
        f"多单约 {fmt_usd(event['long'])}，空单约 {fmt_usd(event['short'])}。",
        (
            f"同期 OKX 现货：比特币 {fmt_price(prices['BTC-USDT']['last'])}（24 小时{fmt_chg(prices['BTC-USDT']['chg24'])}），"
            f"以太坊 {fmt_price(prices['ETH-USDT']['last'])}（24 小时{fmt_chg(prices['ETH-USDT']['chg24'])}），"
            f"SOL {fmt_price(prices['SOL-USDT']['last'])}（24 小时{fmt_chg(prices['SOL-USDT']['chg24'])}）。"
        ),
        PAGE_HOME,
    ]
    return "\n".join(lines)


def send_lark(webhook: str, secret: str | None, text: str) -> dict[str, Any]:
    body = as_news(text)
    heads: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s in ("【历史阈值警报】", "【特殊警报】", "【实时日常报告】"):
            heads.append(s)
        elif not s:
            continue
        else:
            break
    if heads:
        body = "\n".join(heads) + "\n" + body
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": body}}
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
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body) if body else {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--force", action="store_true", help="忽略已发送去重（仍写 state）")
    args = parser.parse_args()

    conf = load_env()
    api_key = (conf.get("COINGLASS_API_KEY") or "").strip()
    if not api_key:
        print("[ERROR] 未配置 COINGLASS_API_KEY", file=sys.stderr)
        return 1

    try:
        prices = fetch_okx()
        liq = fetch_liq_all(api_key)
        liq_coins = fetch_liq_coins(api_key)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError, ValueError) as exc:
        print(f"[skip] 行情不可用: {exc}", file=sys.stderr)
        return 1

    now = datetime.now(TZ_CN)
    state = load_state()
    if args.force:
        state["break_sent"] = {}
        state["break_armed"] = {}
        state["breakup_sent"] = {}
        state["breakup_armed"] = {}
        state["liq_sent"] = {}

    breaks = detect_breaks(prices, state, now)
    breakups = detect_breakups(prices, state, now)
    liq_event = detect_liq(liq, state, now)

    print(
        "prices",
        {k: round(v["last"], 4) for k, v in prices.items()},
        "liq_all",
        round(liq["total"], 2),
        "long",
        round(liq["long"], 2),
        "short",
        round(liq["short"], 2),
        "liq_coins",
        {k: round(v["total"], 2) for k, v in liq_coins.items()},
    )
    print(
        "break_events",
        len(breaks),
        "breakup_events",
        len(breakups),
        "liq_event",
        bool(liq_event),
    )

    messages: list[tuple[str, str, dict[str, Any]]] = []
    for event in breaks + breakups:
        messages.append(
            (event["id"], build_break_text(event, prices, now, liq, liq_coins), event)
        )
    if liq_event:
        messages.append((liq_event["id"], build_liq_text(liq_event, prices), liq_event))

    for msg_id, text, _ in messages:
        print("\n---", msg_id, "---\n", text, sep="")

    # Always refresh last prices so the next cross can be detected.
    state["prices"] = {k: v["last"] for k, v in prices.items()}

    focus_event: dict[str, Any] | None = None
    for _msg_id, _text, event in messages:
        if event.get("kind") in ("break", "breakup"):
            focus_event = event
            break
    if focus_event is None and messages:
        focus_event = messages[0][2]

    try:
        out_path = write_latest_json(prices, liq, liq_coins, now, focus_event)
        print(f"[json] {out_path}", file=sys.stderr)
    except OSError as exc:
        print(f"[WARN] 写 latest.json 失败: {exc}", file=sys.stderr)

    if not args.push:
        save_state(state)
        print("\n[dry-run]", file=sys.stderr)
        return 0

    if not messages:
        save_state(state)
        print("[no-alert]", file=sys.stderr)
        return 0

    webhook = (conf.get("LARK_ALERT_WEBHOOK") or "").strip()
    secret = (conf.get("LARK_ALERT_SECRET") or "").strip() or None
    if not webhook:
        print("[ERROR] 未配置 LARK_ALERT_WEBHOOK", file=sys.stderr)
        return 1

    sent_any = False
    for msg_id, text, event in messages:
        try:
            result = send_lark(webhook, secret, text)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"[ERROR] Lark 失败 {msg_id}: {exc}", file=sys.stderr)
            continue
        ok = str(result.get("code", "0")) == "0"
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(
                f"=== {now.strftime('%Y-%m-%d %H:%M:%S')} id={msg_id} ok={ok} resp={result}\n{text}\n\n"
            )
        if not ok:
            print(f"[ERROR] 推送失败 {msg_id}: {result}", file=sys.stderr)
            continue
        sent_any = True
        kind = event["kind"]
        if kind == "break":
            state.setdefault("break_sent", {})[event["latch"]] = msg_id
        elif kind == "breakup":
            state.setdefault("breakup_sent", {})[event["latch"]] = msg_id
        else:
            state.setdefault("liq_sent", {})[event["day_key"]] = msg_id
        print(f"[pushed] {msg_id}", file=sys.stderr)

    save_state(state)
    return 0 if sent_any or not messages else 1


if __name__ == "__main__":
    raise SystemExit(main())
