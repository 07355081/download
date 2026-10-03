"""HIP-3 按币种日序列。不再整包下载 Dune query 8077453。

历史行原样保留。缺口用 Hyperliquid candleSnapshot 的日 K 补成交额
(收盘价 × 成交量)。当天用 metaAndAssetCtxs 的 dayNtlVlm / (openInterest × markPx)，
这是取样时刻的滚动 24 小时，不是自然日。日期写成取样时的 UTC 日。

分类和 Unified Symbol 与 ASXN query 8077453 的规则一致，这样新旧行能接在同一个币上。
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

INFO_URL = "https://api.hyperliquid.xyz/info"
HISTORY_START = date(2025, 10, 13)
CANDLE_SOURCE = "hyperliquid_candleSnapshot"
SNAPSHOT_SOURCE = "hyperliquid_info_dayNtlVlm"

INDEX_EXACT = {
    "xyz:XYZ100",
    "vntl:MAG7",
    "vntl:SEMIS",
    "vntl:INFOTECH",
    "vntl:ROBOT",
    "vntl:NUCLEAR",
    "vntl:DEFENSE",
    "vntl:BIOTECH",
    "vntl:ENERGY",
    "km:US500",
    "km:USTECH",
    "km:USENERGY",
    "km:SMALL2000",
    "km:GLDMINE",
}
INDEX_SUFFIX = re.compile(r":(EWY|EWJ|USA500|USA100|JP225|JPN225|SP500|KR200|VIX|DXY|XLE|URNM)$")
COMMODITY_SUFFIX = re.compile(
    r":(COPPER|SILVER|GOLD|OIL|PLATINUM|PALLADIUM|WTI|USOIL|BRENTOIL|CL|NATGAS|URANIUM|ALUMINIUM|TTF|GAS)$"
)
FX_SUFFIX = re.compile(r":(JPY|EUR)$")
STOCK_PREFIX = re.compile(r"^(xyz|flx|km|cash):")
KNOWN_PREFIX = {"xyz", "flx", "vntl", "hyna", "km", "cash", "para"}
CRYPTO_EXACT = {"flx:XMR", "flx:BTC", "cash:BTC", "cash:ETH"}


def dune_day(day: date) -> str:
    return day.strftime("%Y-%m-%d 00:00:00.000 UTC")


def day_key(value: object) -> str:
    return str(value)[:10] if value else ""


def coin_symbol_of(coin: str) -> str:
    return coin.split(":", 1)[1] if ":" in coin else coin


def unified_symbol(coin_symbol: str) -> str:
    upper = coin_symbol.upper()
    if upper in {"CL", "OIL"}:
        return "WTI"
    if upper == "BZ":
        return "BRENTOIL"
    if upper == "SILVER":
        return "XAG"
    if upper == "GOLD":
        return "XAU"
    if upper in {"SP500", "US500", "USA500", "SPX500"}:
        return "SPX"
    if upper in {"US100", "USA100", "USTECH", "NAS100"}:
        return "NDX"
    if upper in {"JP225", "JPN225", "NIKJPY"}:
        return "Nikkei 225"
    return upper


def category_of(coin: str) -> str:
    if coin in INDEX_EXACT or INDEX_SUFFIX.search(coin):
        return "Indices/ETFs"
    if coin == "km:USBOND":
        return "Bonds"
    if COMMODITY_SUFFIX.search(coin):
        return "Commodities"
    if FX_SUFFIX.search(coin):
        return "FX"
    if coin.startswith("hyna") or coin.startswith("para") or coin in CRYPTO_EXACT:
        return "Crypto"
    if STOCK_PREFIX.match(coin) and coin != "flx:USDE":
        return "Stocks"
    if coin.startswith("vntl"):
        return "Pre-IPO"
    prefix = coin.split(":", 1)[0]
    if ":" in coin and prefix not in KNOWN_PREFIX:
        return "Stocks"
    return "Crypto"


def info(body: dict) -> object:
    data = json.dumps(body).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(4):
        req = urllib.request.Request(INFO_URL, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in (429, 500, 502, 503):
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Hyperliquid info 失败: {last_error}")


def list_assets() -> list[tuple[str, dict]]:
    """返回 (coin, asset_ctx)。主 perp 盘口不在 HIP-3 里。"""
    dexes = info({"type": "perpDexs"})
    assets: list[tuple[str, dict]] = []
    if not isinstance(dexes, list):
        return assets
    for dex in dexes:
        if not isinstance(dex, dict) or not dex.get("name"):
            continue
        payload = info({"type": "metaAndAssetCtxs", "dex": dex["name"]})
        if not isinstance(payload, list) or len(payload) < 2:
            continue
        meta, ctxs = payload[0], payload[1]
        universe = meta.get("universe") if isinstance(meta, dict) else None
        if not isinstance(universe, list) or not isinstance(ctxs, list):
            continue
        for asset, ctx in zip(universe, ctxs):
            if isinstance(asset, dict) and isinstance(ctx, dict) and asset.get("name"):
                assets.append((str(asset["name"]), ctx))
        time.sleep(0.05)
    return assets


def _num(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("data") if isinstance(payload, dict) else None
    return rows if isinstance(rows, list) else []


def update_hip3_by_symbol(out_dir: Path) -> None:
    dest = out_dir / "hip3_by_symbol.json"
    old_rows = load_rows(dest)
    today = datetime.now(timezone.utc).date()
    today_key = today.isoformat()
    # 滚动 24h 只代表取样当天。日期一过完,丢掉这份快照,改用已经收盘的日 K。
    history = [
        row for row in old_rows
        if not (
            row.get("source") == SNAPSHOT_SOURCE
            and day_key(row.get("Date")) < today_key
        )
    ]
    present = {day_key(row.get("Date")) for row in history}
    present.discard("")
    gap_start = date.fromisoformat(max(present)) + timedelta(days=1) if present else HISTORY_START

    last_oi: dict[str, float] = {}
    for row in sorted(history, key=lambda item: day_key(item.get("Date"))):
        symbol = row.get("Unified Symbol")
        oi = row.get("Open Interest")
        if isinstance(symbol, str) and isinstance(oi, (int, float)):
            last_oi[symbol] = float(oi)

    assets = list_assets()
    print(f"  hip3_by_symbol 官方接口 {len(assets)} 个币,缺口 {gap_start} → {today - timedelta(days=1)}", flush=True)

    candle_acc: dict[tuple, float] = {}
    if gap_start < today:
        start_ms = int(datetime(gap_start.year, gap_start.month, gap_start.day, tzinfo=timezone.utc).timestamp() * 1000)
        end_ms = int(datetime(today.year, today.month, today.day, tzinfo=timezone.utc).timestamp() * 1000)
        for index, (coin, _ctx) in enumerate(assets, 1):
            try:
                candles = info({
                    "type": "candleSnapshot",
                    "req": {"coin": coin, "interval": "1d", "startTime": start_ms, "endTime": end_ms},
                })
            except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
                print(f"    candle {coin} 跳过: {exc}", flush=True)
                continue
            if isinstance(candles, list):
                for candle in candles:
                    if not isinstance(candle, dict):
                        continue
                    opened = datetime.fromtimestamp(int(candle["t"]) / 1000, timezone.utc).date()
                    if opened < gap_start or opened >= today:
                        continue
                    symbol = coin_symbol_of(coin)
                    key = (opened, symbol, unified_symbol(symbol), category_of(coin))
                    candle_acc[key] = candle_acc.get(key, 0.0) + _num(candle.get("v")) * _num(candle.get("c"))
            if index % 50 == 0:
                print(f"    candles {index}/{len(assets)}", flush=True)
            time.sleep(0.08)

    candle_rows = []
    for (opened, symbol, unified, category), volume in candle_acc.items():
        if volume <= 0 and unified not in last_oi:
            continue
        candle_rows.append({
            "Date": dune_day(opened),
            "Coin": symbol,
            "Unified Symbol": unified,
            "Category": category,
            "Daily Volume": volume,
            "Open Interest": last_oi.get(unified),
            "source": CANDLE_SOURCE,
        })

    snapshot_acc: dict[tuple, list[float]] = {}
    for coin, ctx in assets:
        volume = _num(ctx.get("dayNtlVlm"))
        oi = _num(ctx.get("openInterest")) * _num(ctx.get("markPx"))
        if volume <= 0 and oi <= 0:
            continue
        symbol = coin_symbol_of(coin)
        key = (symbol, unified_symbol(symbol), category_of(coin))
        slot = snapshot_acc.setdefault(key, [0.0, 0.0])
        slot[0] += volume
        slot[1] += oi

    kept = [row for row in history if day_key(row.get("Date")) != today_key]
    kept_days = {day_key(row.get("Date")) for row in kept}
    candle_rows = [row for row in candle_rows if day_key(row.get("Date")) not in kept_days]
    snapshot_rows = [
        {
            "Date": dune_day(today),
            "Coin": symbol,
            "Unified Symbol": unified,
            "Category": category,
            "Daily Volume": volume,
            "Open Interest": oi,
            "source": SNAPSHOT_SOURCE,
        }
        for (symbol, unified, category), (volume, oi) in snapshot_acc.items()
    ]
    data = kept + candle_rows + snapshot_rows
    data.sort(key=lambda row: (day_key(row.get("Date")), str(row.get("Unified Symbol") or "")))

    payload = {
        "code": "0",
        "msg": "success",
        "query_id": None,
        "name": "hip3_by_symbol",
        "description": "Hyperliquid HIP-3 按具体资产分类的交易量和持仓量",
        "source_url": "https://api.hyperliquid.xyz/info",
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "execution_id": None,
        "execution_ended_at": None,
        "executed_by_us": False,
        "row_count": len(data),
        "columns": ["Date", "Coin", "Unified Symbol", "Category", "Daily Volume", "Open Interest"],
        "snapshot_note": "当天 source=hyperliquid_info_dayNtlVlm 是取样时的滚动 24 小时成交额;更早的缺口来自 1 日 K 线",
        "data": data,
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(dest)
    print(f"    rows={len(data)} candle_days={len({day_key(r['Date']) for r in candle_rows})} snapshot={len(snapshot_rows)}", flush=True)
