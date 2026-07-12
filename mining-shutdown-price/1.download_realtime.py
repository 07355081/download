"""Step 1/4 — Download realtime mining shutdown snapshot into CSV.

Next: 2.download_hashrate.py → 3.compute_shutdown.py → 4.csv_to_json.py (or `python run_all.py`).

Output:
  - output/csv/realtime_mining_shutdown_price_snapshot.csv
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
from pathlib import Path
from typing import Any

import requests

HERE = Path(__file__).resolve().parent
OUTPUT_CSV = HERE / "output" / "csv" / "realtime_mining_shutdown_price_snapshot.csv"
DEFAULT_ELECTRICITY_CENTS = 5.0

BITMAIN_INIT_URL = "https://www-api.bitmain.com/minerProfitCal/initMinerProfitCal"
BITMAIN_CAL_URL = "https://www-api.bitmain.com/minerProfitCal/calMinerProfit"
BITMAIN_SHOP_MAIN_URL = "https://shop-product-service.bitmain.com/api/productshow/getMainProduct"

FALLBACK_COIN_MODELS: dict[str, list[str]] = {
    "BTC": ["S23e U2H", "S21", "T21", "S19j Pro", "S19"],
    "BCH": ["S23e U2H", "S21", "T21", "S19j Pro", "S19"],
    "BSV": ["S23e U2H", "S21", "T21", "S19j Pro", "S19"],
    "ALPH": ["AL1"],
    "CKB": ["K7"],
    "DASH": ["D9", "D7", "D5", "D3", "R3-DASH", "R1-DASH"],
    "ETC": ["E11"],
    "HNS": ["HS3"],
    "KAS": ["KS7", "KS5 Pro", "KS5", "KS3"],
    "KDA": ["KA3"],
    "LTC+DOGE+BELLS+JKC+LKY+PEP": [
        "L11",
        "L11 HU2",
        "L11 HU6",
        "U2L9H",
        "L9",
        "L7",
        "R3-LTC",
        "L3++",
        "L3+",
        "L3",
    ],
    "XMR": ["X9", "X5", "X3"],
    "ZEC": ["Z15 Pro", "Z15", "Z15J", "Z15E", "Z11", "Z9 mini", "Z9"],
}

MANUAL_MINER_MODEL_ALIASES: dict[str, list[str]] = {
    "s21pro": ["S21 Pro"],
    "s21xphyd": ["S21 XP Hyd."],
    "s21xpphyd": ["S21XP+ Hyd."],
    "s21xp+hyd": ["S21XP+ Hyd."],
    "s21imm": ["S21 lmm.(NEM)", "S21 lmm.(HEM)"],
    "s21xpimm": ["S21 XP Imm.(NEM)", "S21 XP Imm.(HEM)"],
}

CSV_COLUMNS = [
    "coin",
    "minerModel",
    "electricityPriceCents",
    "singlePowerConsumptionW",
    "singleHashRate",
    "singleHashRateUnit",
    "energyEfficiencyRatio",
    "machinePriceUsd",
    "otherCostUsdPerDay",
    "electricityCostUsdPerDay",
    "electricityShutdownPriceUsd",
    "shutdownPriceUsd",
    "paybackDays",
    "roiPercent",
    "dailyTheoreticalProfitUsd",
    "dailyNetProfitUsd",
    "priceSource",
    "fetchedAt",
]


def normalize_electricity_key(value: Any) -> str:
    n = as_float(value)
    if n is None:
        return str(value)
    return f"{n:g}"


def row_key(coin: str, electricity_price_cents: float, miner_model: str) -> tuple[str, str, str]:
    return coin, normalize_electricity_key(electricity_price_cents), miner_model


def row_key_from_record(row: dict[str, Any]) -> tuple[str, str, str] | None:
    coin = str(row.get("coin", "")).strip()
    miner_model = str(row.get("minerModel", "")).strip()
    electricity = row.get("electricityPriceCents")
    if not coin or not miner_model or electricity is None:
        return None
    return coin, normalize_electricity_key(electricity), miner_model


def load_existing_rows(csv_path: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    if not csv_path.exists():
        return {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = [dict(item) for item in reader]
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = row_key_from_record(row)
        if key is None:
            continue
        out[key] = row
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coins", default=",".join(FALLBACK_COIN_MODELS.keys()), help="Comma-separated coins to include.")
    parser.add_argument("--out", default=str(OUTPUT_CSV), help="Output CSV path")
    parser.add_argument(
        "--electricity-cents",
        type=float,
        default=DEFAULT_ELECTRICITY_CENTS,
        help="Electricity price in cents/kWh for the snapshot (default: 5).",
    )
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout seconds")
    return parser.parse_args()


def normalize_text(text: str) -> str:
    return " ".join((text or "").strip().split())


def normalize_miner_model_key(model: str) -> str:
    return "".join(ch for ch in model.lower() if ch.isalnum())


def parse_price_usd(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        value = float(raw.replace(",", "").replace(" ", ""))
    except ValueError:
        return None
    return value if value > 0 else None


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n else None


def as_iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def build_model_candidates(input_model: str) -> list[str]:
    normalized_input = normalize_text(input_model)
    input_key = normalize_miner_model_key(normalized_input)
    candidates: list[str] = []
    seen: set[str] = set()

    def push(model: str) -> None:
        m = normalize_text(model)
        if not m:
            return
        key = m.lower()
        if key in seen:
            return
        seen.add(key)
        candidates.append(m)

    push(normalized_input)
    for alias in MANUAL_MINER_MODEL_ALIASES.get(input_key, []):
        push(alias)
    for models in FALLBACK_COIN_MODELS.values():
        for model in models:
            if model.lower() == normalized_input.lower():
                push(model)
    return candidates


def fetch_shop_quotes(timeout: int) -> list[dict[str, Any]]:
    response = requests.get(BITMAIN_SHOP_MAIN_URL, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if str(payload.get("code")) != "0" or not isinstance(payload.get("data"), list):
        raise RuntimeError(payload.get("message") or "shop.bitmain.com 返回异常")

    merged: dict[str, dict[str, Any]] = {}
    for item in payload["data"]:
        model = normalize_text(str(item.get("categoryName") or ""))
        if not model or model.lower() == "all":
            continue
        if not any(ch.isdigit() for ch in model):
            continue
        name = normalize_text(str(item.get("name") or ""))
        if name and "miner" not in name.lower():
            continue
        currencies = str(item.get("miningCurrency") or "")
        coins = [normalize_text(x) for x in currencies.split(",") if normalize_text(x)]
        currency = str(item.get("currency") or "USD").upper()
        price_usd = parse_price_usd(str(item.get("price") or "")) if currency == "USD" else None

        existing = merged.get(model)
        if existing is None:
            merged[model] = {"model": model, "priceUsd": price_usd, "coins": coins}
            continue
        existing["coins"] = sorted(set(existing["coins"]) | set(coins))
        if existing["priceUsd"] is None and price_usd is not None:
            existing["priceUsd"] = price_usd

    return sorted(merged.values(), key=lambda x: str(x["model"]).lower())


def get_models_for_coin(coin: str, shop_quotes: list[dict[str, Any]]) -> list[str]:
    # Match RealtimeMiningShutdownPanel online mode:
    # use shop models first; fallback to default curated list only if shop is unavailable/empty.
    if shop_quotes:
        models = [
            str(item.get("model", "")).strip()
            for item in shop_quotes
            if str(item.get("model", "")).strip()
            and (not item.get("coins") or coin in (item.get("coins") or []))
        ]
        deduped: list[str] = []
        seen: set[str] = set()
        for model in models:
            key = model.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(model)
        if deduped:
            return deduped
    return FALLBACK_COIN_MODELS[coin]


def request_init_for_model(coin: str, miner_model: str, timeout: int) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = {"coinTypeList": [{"coinType": coin}], "minerModel": miner_model, "language": "en"}
    response = requests.post(BITMAIN_INIT_URL, json=payload, timeout=timeout)
    data = response.json()
    if not response.ok:
        raise RuntimeError(data.get("message") or f"init 失败: HTTP {response.status_code}")
    return payload, data


def find_valid_init(coin: str, miner_model: str, timeout: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    last_error = "初始化失败"
    for candidate in build_model_candidates(miner_model):
        init_payload, init_json = request_init_for_model(coin, candidate, timeout)
        data_node = init_json.get("data")
        if not isinstance(data_node, dict):
            data_node = {}
        coin_type_list = data_node.get("coinTypeList") or []
        candidate_coin = coin_type_list[0] if coin_type_list else {}
        if not isinstance(candidate_coin, dict):
            candidate_coin = {}
        valid = (
            str(init_json.get("code")) == "0"
            and candidate_coin.get("difficulty") is not None
            and candidate_coin.get("coinPrice") is not None
        )
        if valid:
            return init_payload, data_node, candidate_coin
        last_error = str(init_json.get("message") or "初始化失败")
    raise RuntimeError(last_error)


def calculate_single(
    coin: str,
    miner_model: str,
    shop_quotes: list[dict[str, Any]],
    timeout: int,
    electricity_cents: float,
) -> dict[str, Any]:
    init_payload, init_data, coin_config = find_valid_init(coin, miner_model, timeout)
    model_lower = miner_model.lower()
    matched = next(
        (
            q
            for q in shop_quotes
            if str(q.get("model", "")).lower() == model_lower
            and (not q.get("coins") or coin in q.get("coins"))
        ),
        None,
    )
    if matched is None:
        matched = next((q for q in shop_quotes if str(q.get("model", "")).lower() == model_lower), None)

    shop_unit_price = as_float(matched.get("priceUsd")) if matched else None
    api_unit_price = as_float(init_data.get("unitPrice"))
    hash_unit_price = as_float(init_data.get("hashUnitPrice"))
    single_rate = as_float(init_data.get("singleRate"))
    derived_by_hash = hash_unit_price * single_rate if hash_unit_price is not None and single_rate is not None else None
    use_derived = (
        derived_by_hash is not None
        and (api_unit_price is None or api_unit_price <= 0 or (api_unit_price == 1000 and derived_by_hash > api_unit_price))
    )
    machine_price_usd = shop_unit_price if shop_unit_price is not None else (derived_by_hash if use_derived else api_unit_price)
    other_cost_usd_per_day = machine_price_usd / 365 / 4 if machine_price_usd is not None and machine_price_usd > 0 else None
    if shop_unit_price is not None:
        price_source = "shop"
    elif use_derived:
        price_source = "api_hash"
    else:
        price_source = "api"

    cal_payload_base = {
        "language": "en",
        "coinTypeList": [
            {
                "coinType": coin,
                "difficulty": float(coin_config.get("difficulty")),
                "coinPrice": f"{float(coin_config.get('coinPrice')):.4f}",
            }
        ],
        "unitPrice": str(machine_price_usd if machine_price_usd is not None else 0),
        "hashUnitPrice": "0",
        "minerNumber": init_data.get("minerNumber", 1),
        "electricityPrice": electricity_cents,
        "startTime": init_data.get("startTime"),
        "endTime": init_data.get("endTime"),
        "singlePowerConsumption": init_data.get("singlePowerConsumption"),
        "singleRate": str(single_rate if single_rate is not None else 0),
        "hashRateUnit": init_data.get("hashRateUnit", "T"),
        "minerDifficulty": 0,
        "jt": init_data.get("jt", 10),
    }

    # 口径1：Other Costs = 0，取关机价
    cal_payload_shutdown = dict(cal_payload_base)
    cal_payload_shutdown["otherExpenses"] = 0
    shutdown_res = requests.post(BITMAIN_CAL_URL, json=cal_payload_shutdown, timeout=timeout)
    shutdown_json = shutdown_res.json()
    if not shutdown_res.ok or str(shutdown_json.get("code")) != "0" or not isinstance(shutdown_json.get("data"), dict):
        raise RuntimeError(shutdown_json.get("message") or f"计算失败(关机价): HTTP {shutdown_res.status_code}")
    shutdown_data = shutdown_json["data"]
    shutdown_day_profit = shutdown_data.get("dayProfit") if isinstance(shutdown_data.get("dayProfit"), dict) else {}

    # 口径2：Other Costs = Miner Cost / 365 / 4，取盈亏平衡价、回本天数、ROI
    cal_payload_breakeven = dict(cal_payload_base)
    cal_payload_breakeven["otherExpenses"] = other_cost_usd_per_day or 0
    breakeven_res = requests.post(BITMAIN_CAL_URL, json=cal_payload_breakeven, timeout=timeout)
    breakeven_json = breakeven_res.json()
    if not breakeven_res.ok or str(breakeven_json.get("code")) != "0" or not isinstance(breakeven_json.get("data"), dict):
        raise RuntimeError(breakeven_json.get("message") or f"计算失败(盈亏平衡): HTTP {breakeven_res.status_code}")
    breakeven_data = breakeven_json["data"]
    breakeven_day_profit = breakeven_data.get("dayProfit") if isinstance(breakeven_data.get("dayProfit"), dict) else {}

    return_on_invest_raw = breakeven_data.get("returnOnInvest")
    roi_percent = None
    if isinstance(return_on_invest_raw, (int, float)):
        roi_percent = float(return_on_invest_raw) * 100
    elif isinstance(return_on_invest_raw, str):
        try:
            roi_percent = float(return_on_invest_raw) * 100
        except ValueError:
            roi_percent = None

    electricity_cost_usd_per_day = as_float(shutdown_day_profit.get("electricityAmount"))
    electricity_shutdown_price_usd = as_float(shutdown_data.get("closeMinerCoinPrice"))
    shutdown_price_usd = as_float(breakeven_data.get("closeMinerCoinPrice"))

    single_power = as_float(init_data.get("singlePowerConsumption"))
    hash_rate_unit = str(init_data.get("hashRateUnit") or "T").strip() or "T"
    energy_efficiency_ratio = single_power / single_rate if single_power is not None and single_rate is not None and single_rate > 0 else None

    return {
        "coin": coin,
        "minerModel": miner_model,
        "electricityPriceCents": electricity_cents,
        "singlePowerConsumptionW": single_power,
        "singleHashRate": single_rate,
        "singleHashRateUnit": hash_rate_unit,
        "energyEfficiencyRatio": energy_efficiency_ratio,
        "machinePriceUsd": machine_price_usd,
        "otherCostUsdPerDay": other_cost_usd_per_day,
        "electricityCostUsdPerDay": electricity_cost_usd_per_day,
        "electricityShutdownPriceUsd": electricity_shutdown_price_usd,
        "shutdownPriceUsd": shutdown_price_usd,
        "paybackDays": as_float(breakeven_data.get("payBackDays")),
        "roiPercent": roi_percent,
        "dailyTheoreticalProfitUsd": as_float(breakeven_day_profit.get("outMoney")),
        "dailyNetProfitUsd": as_float(breakeven_day_profit.get("netProfit")),
        "priceSource": price_source,
        "fetchedAt": as_iso_now(),
    }


def main() -> None:
    args = parse_args()
    electricity_cents = max(0.0, float(args.electricity_cents))
    selected_coins = [x.strip() for x in str(args.coins).split(",") if x.strip()]
    invalid = [coin for coin in selected_coins if coin not in FALLBACK_COIN_MODELS]
    if invalid:
        raise SystemExit(f"Unsupported coins: {', '.join(invalid)}")
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    existing_by_key = load_existing_rows(out_path)
    try:
        shop_quotes = fetch_shop_quotes(timeout=args.timeout)
    except Exception as exc:
        print(f"[warn] shop quotes unavailable: {exc}; using fallback model lists")
        shop_quotes = []
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    kept_old_count = 0
    touched_keys: set[tuple[str, str, str]] = set()

    for coin in selected_coins:
        models = get_models_for_coin(coin, shop_quotes)
        for model in models:
            key = row_key(coin, electricity_cents, model)
            touched_keys.add(key)
            try:
                row = calculate_single(
                    coin=coin,
                    miner_model=model,
                    shop_quotes=shop_quotes,
                    timeout=args.timeout,
                    electricity_cents=electricity_cents,
                )
                rows.append(row)
                print(f"[ok] coin={coin} electricity={electricity_cents} model={model}")
            except Exception as exc:
                old_row = existing_by_key.get(key)
                if old_row is not None:
                    rows.append(old_row)
                    kept_old_count += 1
                    msg = f"[fail] coin={coin} electricity={electricity_cents} model={model} err={exc} -> kept previous row"
                else:
                    msg = f"[fail] coin={coin} electricity={electricity_cents} model={model} err={exc}"
                failures.append(msg)
                print(msg)

    # Keep rows that were not part of this run scope to avoid accidental truncation.
    untouched_keys = [k for k in existing_by_key.keys() if k not in touched_keys]
    for key in untouched_keys:
        rows.append(existing_by_key[key])

    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in CSV_COLUMNS})

    print(f"wrote csv={out_path} rows={len(rows)}")
    if kept_old_count:
        print(f"kept_previous_rows={kept_old_count}")
    if untouched_keys:
        print(f"kept_untouched_rows={len(untouched_keys)}")
    if failures:
        print(f"failures={len(failures)}")


if __name__ == "__main__":
    main()

