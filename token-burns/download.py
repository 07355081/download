"""Build daily token-burn JSON for UNI, PUMP, PONS, and HYPE.

No new Dune queries.

  UNI   existing uni-burn/output/json/daily_by_chain.json
        plus CoinGecko price, market cap, and implied circulating supply
  PUMP  https://fees.pump.fun/api/buybacks (USD). Token count is USD / daily close.
  PONS  https://pons-metrics.simplethin.gs/data/history.csv
  HYPE  current Assistance Fund balance from the Hyperliquid info API.
        This file keeps each UTC day's balance from the first run onward.
        There is no backfill. DefiLlama fees are attached as context only.

Outputs:
  output/json/{uni,pump,pons,hype}.json

Usage:
  python download.py
  python download.py --only uni
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT_DIR = HERE.parent
JSON_DIR = HERE / "output" / "json"
UNI_BURN_JSON = ROOT_DIR / "uni-burn" / "output" / "json" / "daily_by_chain.json"
HL_FEES_JSON = ROOT_DIR / "hyperliquid" / "output" / "json" / "hl_fees_daily.json"

PUMP_BUYBACK_URL = "https://fees.pump.fun/api/buybacks"
PUMP_LLAMA_URL = "https://api.llama.fi/summary/fees/pump.fun?dataType=dailyHoldersRevenue"
GECKO_DAYS = "365"
PONS_CSV_URL = "https://pons-metrics.simplethin.gs/data/history.csv"
HL_INFO_URL = "https://api.hyperliquid.xyz/info"
ASSISTANCE_FUND = "0xfefefefefefefefefefefefefefefefefefefefe"
GECKO_BASE = "https://api.coingecko.com/api/v3"

UNI_MAX = 1_000_000_000
# query.sql 把提案销毁那笔交易记成 1 亿 UNI。窄查询只保留之后的日度文件，这笔不在本地历史里。
UNI_PROPOSAL_BURN = 100_000_000
PUMP_MAX = 1_000_000_000_000
PONS_MAX = 1_000_000_000
HYPE_MAX = 1_000_000_000

GECKO_IDS = {
    "uni": "uniswap",
    "pump": "pump-fun",
    "hype": "hyperliquid",
}

USER_AGENT = "dashboard-data/token-burns"
GECKO_GAP_S = 2.2


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def gecko_key() -> str:
    return (os.environ.get("COINGECKO_DEMO_API_KEY") or os.environ.get("COINGECKO_KEY") or "").strip()


def http_bytes(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    attempts: int = 3,
) -> bytes:
    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json,text/csv,*/*"}
    if headers:
        req_headers.update(headers)
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < attempts:
                time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"{method} {url} failed: {last}")


def http_json(url: str, *, method: str = "GET", payload: dict | None = None, attempts: int = 3) -> object:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if body else None
    raw = http_bytes(url, method=method, body=body, headers=headers, attempts=attempts)
    return json.loads(raw.decode("utf-8"))


def day_key(value: object) -> str:
    return str(value or "").strip()[:10]


def shift_day(day: str, offset: int) -> str:
    stamp = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return (stamp + timedelta(days=offset)).date().isoformat()


def num(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


def ms_to_day(ms: object) -> str:
    if not isinstance(ms, (int, float)):
        return ""
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).date().isoformat()


class Gecko:
    def __init__(self) -> None:
        self._last = 0.0
        self._charts: dict[str, dict[str, dict[str, float]]] = {}
        self._spots: dict[str, dict[str, float | None]] = {}

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if self._last and elapsed < GECKO_GAP_S:
            time.sleep(GECKO_GAP_S - elapsed)
        self._last = time.monotonic()

    def _get(self, path: str) -> object:
        self._wait()
        key = gecko_key()
        headers = {"x-cg-demo-api-key": key} if key else None
        print(f"    GET coingecko {path.split('?')[0]}")
        raw = http_bytes(f"{GECKO_BASE}{path}", headers=headers)
        return json.loads(raw.decode("utf-8"))

    def chart(self, coin_id: str) -> dict[str, dict[str, float]]:
        if coin_id in self._charts:
            return self._charts[coin_id]
        try:
            payload = self._get(f"/coins/{coin_id}/market_chart?vs_currency=usd&days={GECKO_DAYS}")
        except Exception as exc:  # noqa: BLE001
            print(f"    [WARN] coingecko chart {coin_id}: {exc}", file=sys.stderr)
            self._charts[coin_id] = {}
            return {}
        if not isinstance(payload, dict):
            raise RuntimeError(f"unexpected coingecko chart for {coin_id}")
        by_day: dict[str, dict[str, float]] = {}
        for key, field in (("prices", "price"), ("market_caps", "market_cap")):
            series = payload.get(key) or []
            if not isinstance(series, list):
                continue
            for point in series:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                day = ms_to_day(point[0])
                value = num(point[1])
                if not day or value is None or value <= 0:
                    continue
                by_day.setdefault(day, {})[field] = value
        self._charts[coin_id] = by_day
        return by_day

    def spot(self, coin_id: str) -> dict[str, float | None]:
        if coin_id in self._spots:
            return self._spots[coin_id]
        try:
            payload = self._get(
                f"/coins/{coin_id}?localization=false&tickers=false&market_data=true"
                "&community_data=false&developer_data=false&sparkline=false"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"    [WARN] coingecko spot {coin_id}: {exc}", file=sys.stderr)
            spot = {"price": None, "market_cap": None, "circulating": None}
            self._spots[coin_id] = spot
            return spot
        if not isinstance(payload, dict):
            raise RuntimeError(f"unexpected coingecko coin for {coin_id}")
        market = payload.get("market_data") or {}
        if not isinstance(market, dict):
            market = {}
        price = _nested_usd(market.get("current_price"))
        mcap = _nested_usd(market.get("market_cap"))
        circulating = num(market.get("circulating_supply"))
        spot = {"price": price, "market_cap": mcap, "circulating": circulating}
        self._spots[coin_id] = spot
        return spot


def _nested_usd(value: object) -> float | None:
    if isinstance(value, dict):
        return num(value.get("usd"))
    return num(value)


def quote(
    chart: dict[str, dict[str, float]],
    day: str,
    extra_prices: dict[str, float] | None = None,
) -> tuple[float | None, float | None, float | None]:
    row = chart.get(day) or {}
    price = row.get("price")
    if not price and extra_prices:
        price = extra_prices.get(day)
    mcap = row.get("market_cap")
    circulating = None
    if price and mcap and price > 0:
        circulating = mcap / price
    return price, mcap, circulating


def llama_daily_prices(coin_id: str, start_ts: int, span: int) -> dict[str, float]:
    url = (
        f"https://coins.llama.fi/chart/coingecko:{coin_id}"
        f"?start={start_ts}&span={span}&period=1d&searchWidth=600"
    )
    print(f"    GET defillama coins {coin_id}")
    payload = http_json(url)
    if not isinstance(payload, dict):
        return {}
    coins = payload.get("coins")
    if not isinstance(coins, dict) or not coins:
        return {}
    series = next(iter(coins.values()))
    points = series.get("prices") if isinstance(series, dict) else None
    if not isinstance(points, list):
        return {}
    out: dict[str, float] = {}
    for point in points:
        if not isinstance(point, dict):
            continue
        stamp = num(point.get("timestamp"))
        price = num(point.get("price"))
        if stamp is None or price is None or price <= 0:
            continue
        out[datetime.fromtimestamp(int(stamp), timezone.utc).date().isoformat()] = price
    return out


def apply_spot(rows: list[dict], spot: dict[str, float | None]) -> None:
    if not rows:
        return
    last = rows[-1]
    price = spot.get("price")
    mcap = spot.get("market_cap")
    circulating = spot.get("circulating")
    if isinstance(price, float) and price > 0:
        last["price_usd"] = price
    if isinstance(mcap, float) and mcap > 0:
        last["market_cap_usd"] = mcap
    if isinstance(circulating, float) and circulating > 0:
        last["circulating"] = circulating


def read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def envelope(
    name: str,
    *,
    description: str,
    source_url: str,
    max_supply: float,
    token_count_basis: str,
    supply_basis: str,
    rows: list[dict],
    extra: dict | None = None,
) -> dict:
    latest_price = None
    as_of = None
    for row in reversed(rows):
        price = row.get("price_usd")
        if isinstance(price, (int, float)) and price > 0:
            latest_price = price
            as_of = row.get("day")
            break
    payload = {
        "code": "0",
        "msg": "success",
        "name": name,
        "description": description,
        "source_url": source_url,
        "updated_at": now_utc_iso(),
        "max_supply": max_supply,
        "token_count_basis": token_count_basis,
        "supply_basis": supply_basis,
        "as_of": as_of,
        "latest_price_usd": latest_price,
        "columns": [
            "day",
            "burned_tokens",
            "burned_usd",
            "price_usd",
            "cumulative_burned",
            "circulating",
            "market_cap_usd",
        ],
        "row_count": len(rows),
        "data": rows,
    }
    if extra:
        payload.update(extra)
    return payload


def build_uni(gecko: Gecko) -> dict:
    raw = read_json(UNI_BURN_JSON)
    if not raw or not isinstance(raw.get("data"), list):
        raise RuntimeError(f"missing UNI burn file: {UNI_BURN_JSON}")
    by_day: dict[str, dict[str, float]] = {}
    for row in raw["data"]:
        if not isinstance(row, dict):
            continue
        day = day_key(row.get("day"))
        if not day:
            continue
        bucket = by_day.setdefault(day, {"burned": 0.0, "usd": 0.0, "proposal": 0.0})
        tokens = num(row.get("total_uni_burned")) or 0.0
        usd = num(row.get("total_uni_burned_usd")) or 0.0
        label = str(row.get("label") or "")
        if label == "Proposal Burn":
            bucket["proposal"] += tokens
        else:
            bucket["burned"] += tokens
            bucket["usd"] += usd
    chart = gecko.chart(GECKO_IDS["uni"])
    rows: list[dict] = []
    proposal_in_file = sum(bucket["proposal"] for bucket in by_day.values())
    # 文件若从提案销毁日之后才开始，累计仍要把那 1 亿算进去，日度柱不画它。
    opening_proposal = 0.0 if proposal_in_file > 0 else float(UNI_PROPOSAL_BURN)
    cumulative = opening_proposal
    proposal_total = opening_proposal
    for day in sorted(by_day):
        bucket = by_day[day]
        proposal_total += bucket["proposal"]
        cumulative += bucket["burned"] + bucket["proposal"]
        price, mcap, circulating = quote(chart, day)
        rows.append(
            {
                "day": day,
                "burned_tokens": bucket["burned"],
                "burned_usd": bucket["usd"],
                "proposal_burn_tokens": bucket["proposal"],
                "price_usd": price,
                "cumulative_burned": cumulative,
                "circulating": circulating,
                "market_cap_usd": mcap,
            }
        )
    apply_spot(rows, gecko.spot(GECKO_IDS["uni"]))
    return envelope(
        "uni",
        description="UNI 日度销毁、流通量与市值",
        source_url="https://dune.com/queries/8884655",
        max_supply=UNI_MAX,
        token_count_basis="measured",
        supply_basis="circulating",
        rows=rows,
        extra={"proposal_burn_tokens": proposal_total},
    )


def pump_buybacks() -> tuple[dict[str, float], str]:
    """Official buyback JSON, or DefiLlama holders revenue when that URL redirects to HTML."""
    try:
        payload = http_json(PUMP_BUYBACK_URL, attempts=1)
    except Exception as exc:  # noqa: BLE001
        print(f"    [WARN] pump official buybacks unavailable: {exc}")
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("dailyBuybacks"), dict):
        out: dict[str, float] = {}
        for raw_day, raw_usd in payload["dailyBuybacks"].items():
            day = day_key(raw_day)
            usd = num(raw_usd)
            if day and usd is not None and usd > 0:
                out[day] = usd
        if out:
            return out, PUMP_BUYBACK_URL
    llama = http_json(PUMP_LLAMA_URL)
    if not isinstance(llama, dict) or not isinstance(llama.get("totalDataChart"), list):
        raise RuntimeError("no PUMP buyback series from fees.pump.fun or DefiLlama")
    out = {}
    for point in llama["totalDataChart"]:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        stamp = num(point[0])
        usd = num(point[1])
        if stamp is None or usd is None or usd <= 0:
            continue
        day = datetime.fromtimestamp(int(stamp), timezone.utc).date().isoformat()
        out[day] = usd
    return out, "https://defillama.com/protocol/pump.fun"


def build_pump(gecko: Gecko) -> dict:
    buybacks, source_url = pump_buybacks()
    # CoinGecko demo 只能取近 365 天。更早的收盘价用 DefiLlama 的 CoinGecko 日价补上，否则回购初期的数量会漏计。
    extra_prices = llama_daily_prices("pump-fun", 1_747_267_200, 500)
    chart = gecko.chart(GECKO_IDS["pump"])
    rows: list[dict] = []
    cumulative = 0.0
    for raw_day, raw_usd in sorted(buybacks.items()):
        day = day_key(raw_day)
        usd = num(raw_usd)
        if not day or usd is None:
            continue
        price, mcap, circulating = quote(chart, day, extra_prices)
        convert_at = price
        if not convert_at:
            for back in range(1, 4):
                previous, _, _ = quote(chart, shift_day(day, -back), extra_prices)
                if previous:
                    convert_at = previous
                    break
        tokens = (usd / convert_at) if convert_at and convert_at > 0 else None
        if tokens is not None:
            cumulative += tokens
        rows.append(
            {
                "day": day,
                "burned_tokens": tokens,
                "burned_usd": usd,
                "price_usd": price,
                "cumulative_burned": cumulative,
                "circulating": circulating,
                "market_cap_usd": mcap,
            }
        )
    apply_spot(rows, gecko.spot(GECKO_IDS["pump"]))
    return envelope(
        "pump",
        description="PUMP 回购销毁。数量由当日美元除以 CoinGecko 日收盘价换算。",
        source_url=source_url,
        max_supply=PUMP_MAX,
        token_count_basis="derived_from_usd",
        supply_basis="circulating",
        rows=rows,
    )


def build_pons() -> dict:
    raw = http_bytes(PONS_CSV_URL).decode("utf-8")
    reader = csv.DictReader(io.StringIO(raw))
    rows: list[dict] = []
    running = 0.0
    for src in reader:
        day = day_key(src.get("date"))
        if not day:
            continue
        burned = num(src.get("burned_pons"))
        cumulative = num(src.get("burned_cumulative_pons"))
        if cumulative is None and burned is not None:
            running += burned
            cumulative = running
        elif cumulative is not None:
            running = cumulative
        price = num(src.get("pons_price_usd"))
        mcap = num(src.get("pons_market_cap_usd"))
        if price is not None and price <= 0:
            price = None
        if mcap is not None and mcap <= 0:
            mcap = None
        circulating = None if cumulative is None else max(PONS_MAX - cumulative, 0.0)
        rows.append(
            {
                "day": day,
                "burned_tokens": burned,
                "burned_usd": (burned * price) if burned is not None and price else None,
                "price_usd": price,
                "cumulative_burned": cumulative,
                "circulating": circulating,
                "market_cap_usd": mcap,
            }
        )
    rows.sort(key=lambda row: row["day"])
    return envelope(
        "pons",
        description="PONS 销毁。来源是 pons-metrics 公开日表，不是本仓库扫链。",
        source_url=PONS_CSV_URL,
        max_supply=PONS_MAX,
        token_count_basis="measured",
        supply_basis="burn_adjusted",
        rows=rows,
    )


def hype_balance() -> float:
    payload = http_json(
        HL_INFO_URL,
        method="POST",
        payload={"type": "spotClearinghouseState", "user": ASSISTANCE_FUND},
    )
    if not isinstance(payload, dict):
        raise RuntimeError("hyperliquid info returned an unexpected payload")
    balances = payload.get("balances")
    if not isinstance(balances, list):
        raise RuntimeError("hyperliquid info response has no balances")
    for row in balances:
        if isinstance(row, dict) and str(row.get("coin") or "").upper() == "HYPE":
            total = num(row.get("total"))
            if total is None:
                break
            return total
    raise RuntimeError("assistance fund response has no HYPE balance")


def fee_context() -> list[dict]:
    raw = read_json(HL_FEES_JSON)
    if not raw or not isinstance(raw.get("data"), list):
        return []
    out: list[dict] = []
    for row in raw["data"]:
        if not isinstance(row, dict):
            continue
        day = day_key(row.get("day"))
        if not day:
            continue
        out.append(
            {
                "day": day,
                "fees_usd": num(row.get("fees_usd")),
                "revenue_usd": num(row.get("revenue_usd")),
            }
        )
    out.sort(key=lambda row: row["day"])
    return out


def build_hype(gecko: Gecko, dest: Path) -> dict:
    previous = read_json(dest)
    prior_rows = previous.get("data") if previous and isinstance(previous.get("data"), list) else []
    by_day: dict[str, dict] = {}
    for row in prior_rows:
        if not isinstance(row, dict):
            continue
        day = day_key(row.get("day"))
        if day:
            by_day[day] = dict(row)
    balance = hype_balance()
    by_day[today_utc()] = {
        "day": today_utc(),
        "cumulative_burned": balance,
    }
    chart = gecko.chart(GECKO_IDS["hype"])
    rows: list[dict] = []
    days = sorted(by_day)
    previous_cumulative: float | None = None
    for day in days:
        cumulative = num(by_day[day].get("cumulative_burned"))
        burned = None
        if cumulative is not None and previous_cumulative is not None:
            burned = cumulative - previous_cumulative
        price, mcap, circulating = quote(chart, day)
        row = {
            "day": day,
            "burned_tokens": burned,
            "burned_usd": (burned * price) if burned is not None and price else None,
            "price_usd": price,
            "cumulative_burned": cumulative,
            "circulating": circulating,
            "market_cap_usd": mcap,
        }
        rows.append(row)
        if cumulative is not None:
            previous_cumulative = cumulative
    apply_spot(rows, gecko.spot(GECKO_IDS["hype"]))
    return envelope(
        "hype",
        description="HYPE Assistance Fund 当前余额。日度序列只从上线后的快照开始，不回补。",
        source_url="https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees",
        max_supply=HYPE_MAX,
        token_count_basis="balance_snapshot",
        supply_basis="circulating",
        rows=rows,
        extra={
            "history_starts_on": rows[0]["day"] if rows else None,
            "fees": fee_context(),
            "fees_note": "DefiLlama 手续费只作收入背景，不换算成销毁数量。",
        },
    )


def write_payload(dest: Path, payload: dict) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"    wrote {dest.name} rows={payload.get('row_count')}")


def main() -> None:
    load_env_file(ROOT_DIR / ".env")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", choices=("uni", "pump", "pons", "hype"), default=None)
    parser.add_argument("--out", default=str(JSON_DIR))
    args = parser.parse_args()
    out = Path(args.out)
    gecko = Gecko()
    jobs = ["uni", "pump", "pons", "hype"] if args.only is None else [args.only]
    failures: list[str] = []
    for name in jobs:
        dest = out / f"{name}.json"
        print(f"[token-burns] {name}")
        try:
            if name == "uni":
                payload = build_uni(gecko)
            elif name == "pump":
                payload = build_pump(gecko)
            elif name == "pons":
                payload = build_pons()
            else:
                payload = build_hype(gecko, dest)
            write_payload(dest, payload)
        except Exception as exc:  # noqa: BLE001
            failures.append(name)
            print(f"    [WARN] {name} failed: {exc}", file=sys.stderr)
    if failures:
        raise SystemExit(f"token-burns failed: {', '.join(failures)}")


if __name__ == "__main__":
    main()
