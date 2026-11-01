"""Deribit 上 BTC、ETH 最近一个到期日的期权。

用已经在拉的 CoinGlass /api/option/max-pain。
每个到期日有张数、Put/Call、最大痛点、名义价值。
再算这个到期日占该币全部期权持仓的比例，以及全部持仓较前一日的升降。

成交热度和 Gex 这个接口没有，不写进结果。

2026-10-15 的最近到期必须仍是 2026-10-16 的 BTC，张数约 18117，最大痛点 83000。
这只是核对计算没写错，不是去重现 9 月 4 日那篇旧稿。

输出：
  output/json/options_expiry.json
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "coinglass-history"))

import _common as C  # noqa: E402

OUT_PATH = HERE / "output" / "json" / "options_expiry.json"
CACHE_CANDIDATES = [
    ROOT / "coinglass-history" / "option-max-pain-history" / "cache",
    Path("/root/data-download/coinglass-history/option-max-pain-history/cache"),
]
CHECK_DAY = "2026-10-15"
CHECK_EXPIRY = "2026-10-16"
CHECK_BTC_CONTRACTS = 18117.0
CHECK_BTC_PAIN = 83000


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def decode_code(code: str) -> str:
    text = str(code)
    if len(text) != 6 or not text.isdigit():
        raise ValueError(f"无法识别到期日 {code}")
    return f"20{text[0:2]}-{text[2:4]}-{text[4:6]}"


def day_code(day: str) -> str:
    return day[2:4] + day[5:7] + day[8:10]


def num(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def front_row(batch: list[dict], day: str) -> dict | None:
    code = day_code(day)
    future = [row for row in batch if str(row.get("date") or "") >= code]
    if not future:
        return None
    row = min(future, key=lambda item: str(item.get("date")))
    call = num(row.get("call_open_interest"))
    put = num(row.get("put_open_interest"))
    notional = num(row.get("call_open_interest_notional")) + num(row.get("put_open_interest_notional"))
    total_contracts = 0.0
    total_notional = 0.0
    for item in batch:
        total_contracts += num(item.get("call_open_interest")) + num(item.get("put_open_interest"))
        total_notional += num(item.get("call_open_interest_notional")) + num(item.get("put_open_interest_notional"))
    pain = num(row.get("max_pain_price"))
    return {
        "expiry": decode_code(str(row.get("date"))),
        "contracts": round(call + put, 1),
        "call": round(call, 1),
        "put": round(put, 1),
        "pcr": round(put / call, 4) if call else None,
        "max_pain": int(pain) if pain else None,
        "notional": int(round(notional)),
        "share": round((call + put) / total_contracts, 4) if total_contracts else None,
        "total_contracts": round(total_contracts, 1),
        "total_notional": int(round(total_notional)),
    }


def batches_by_day(path: Path) -> dict[str, list[dict]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    grouped: dict[int, list[dict]] = {}
    for snap in doc.get("snapshots") or []:
        ts = snap.get("fetch_time_ms")
        if not isinstance(ts, (int, float)):
            continue
        grouped.setdefault(int(ts), []).append(snap)
    by_day: dict[str, tuple[int, list[dict]]] = {}
    for ts, rows in grouped.items():
        day = datetime.fromtimestamp(ts / 1000, timezone.utc).strftime("%Y-%m-%d")
        prev = by_day.get(day)
        if prev is None or ts >= prev[0]:
            by_day[day] = (ts, rows)
    return {day: rows for day, (_ts, rows) in by_day.items()}


def cache_dir() -> Path | None:
    for path in CACHE_CANDIDATES:
        if (path / "BTC_Deribit.json").is_file() and (path / "ETH_Deribit.json").is_file():
            return path
    return None


def history_from_cache(folder: Path) -> list[dict]:
    btc = batches_by_day(folder / "BTC_Deribit.json")
    eth = batches_by_day(folder / "ETH_Deribit.json")
    rows = []
    for day in sorted(set(btc) & set(eth)):
        btc_row = front_row(btc[day], day)
        eth_row = front_row(eth[day], day)
        if not btc_row or not eth_row:
            continue
        rows.append({"time": day, "btc": btc_row, "eth": eth_row})
    return rows


def live_batch(symbol: str) -> list[dict]:
    status, body = C.http_get_json(
        C.API_BASE + "/api/option/max-pain",
        params={"symbol": symbol, "exchange": "Deribit"},
        api_key=C.resolve_api_key(),
        retries=2,
    )
    if status != 200 or not isinstance(body, dict) or str(body.get("code")) != "0":
        raise RuntimeError(f"{symbol} max-pain 失败 status={status}")
    data = body.get("data")
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"{symbol} max-pain 没有到期日")
    return [row for row in data if isinstance(row, dict)]


def main() -> None:
    folder = cache_dir()
    if folder is None:
        raise SystemExit("找不到 Deribit 期权最大痛点缓存")
    history = history_from_cache(folder)
    if len(history) < 30:
        raise SystemExit(f"历史只有 {len(history)} 天")
    check = next((row for row in history if row["time"] == CHECK_DAY), None)
    if check is None:
        raise SystemExit(f"缺少核对日 {CHECK_DAY}")
    btc = check["btc"]
    if btc["expiry"] != CHECK_EXPIRY or btc["max_pain"] != CHECK_BTC_PAIN:
        raise SystemExit(f"{CHECK_DAY} BTC 对不上：{btc['expiry']} {btc['max_pain']}")
    if abs(btc["contracts"] - CHECK_BTC_CONTRACTS) / CHECK_BTC_CONTRACTS > 0.02:
        raise SystemExit(f"{CHECK_DAY} BTC 张数是 {btc['contracts']}，不是约 {CHECK_BTC_CONTRACTS}")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        btc_live = front_row(live_batch("BTC"), today)
        time.sleep(0.4)
        eth_live = front_row(live_batch("ETH"), today)
    except Exception as exc:  # noqa: BLE001
        print(f"[options-expiry] 当天接口失败，先用缓存: {exc}", flush=True)
        btc_live = None
        eth_live = None
    if btc_live and eth_live:
        history = [row for row in history if row["time"] != today]
        history.append({"time": today, "btc": btc_live, "eth": eth_live})
        history.sort(key=lambda row: row["time"])

    latest = history[-1]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "source": "CoinGlass Deribit /api/option/max-pain",
        "updatedAt": now_iso(),
        "history": history,
    }
    OUT_PATH.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(
        f"[options-expiry] days={len(history)} as_of={latest['time']} "
        f"btc={latest['btc']['expiry']} {latest['btc']['contracts']} "
        f"eth={latest['eth']['expiry']} {latest['eth']['contracts']} -> {OUT_PATH}",
        flush=True,
    )


if __name__ == "__main__":
    main()
