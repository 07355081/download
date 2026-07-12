# -*- coding: utf-8 -*-
"""CoinGecko 原始成交量（BTC）→ major_vol_切勿删除.csv"""
from __future__ import annotations

import argparse
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

API_KEY = os.environ.get("COINGECKO_DEMO_API_KEY", "CG-6QESrrBSVRdymKvuhxVXSyEp")
API_V3_BASE = "https://api.coingecko.com/api/v3"
EXCHANGES_BASE_URL = f"{API_V3_BASE}/exchanges"
EXCHANGES_LIST_URL = f"{EXCHANGES_BASE_URL}/list"
MAX_REQUESTS_PER_MINUTE = 500
MAX_DAYS = 365
# CoinGecko days<=14 返回小时级点；累加会虚增约 24x。仅使用日级粒度选项。
SUPPORTED_DAYS = (30, 90, 180, 365)
MIN_API_DAYS = 30
DATE_COL = "日期"
TOTAL_COL = "ALL"

BASE_DIR = Path(__file__).resolve().parent
TARGET_CSV = BASE_DIR / "major_vol_切勿删除.csv"

EXCHANGE_SPOT_FUTURES_PAIRS: list[tuple[str, str | None]] = [
    ("binance", "binance_futures"),
    ("bitfinex", "bitfinex_futures"),
    ("bitget", "bitget_futures"),
    ("bitmart", "bitmart_futures"),
    ("bybit_spot", "bybit"),
    ("crypto_com", "crypto_com_futures"),
    ("deribit_spot", "deribit"),
    ("gate", "gate_futures"),
    ("huobi", "huobi_dm"),
    ("kucoin", "kumex"),
    ("mxc", "mxc_futures"),
    ("okex", "okex_swap"),
    ("gdax", "coinbase_international_derivatives"),
    ("kraken", "kraken_futures"),
    ("hyperliquid-spot", "hyperliquid"),
    ("upbit", None),
]
UNISWAP_COL = "uniswap"
UNISWAP_ID_RE = re.compile(r"(?i)uniswap")
UNISWAP_SKIP_NAME_RE = re.compile(r"sakura", re.I)
HEADERS = {"x-cg-demo-api-key": API_KEY}


def build_columns() -> list[str]:
    cols: list[str] = []
    for spot, fut in EXCHANGE_SPOT_FUTURES_PAIRS:
        cols.append(spot)
        if fut:
            cols.append(fut)
    cols.extend([UNISWAP_COL, TOTAL_COL])
    return cols


COLUMNS = build_columns()
DOWNLOAD_COLUMNS = [c for c in COLUMNS if c != TOTAL_COL]


class RateLimiter:
    def __init__(self, cap: int):
        self.cap = cap
        self.times: list[float] = []
        self.count = 0

    def wait(self) -> None:
        now = time.time()
        self.times = [t for t in self.times if now - t < 60]
        if len(self.times) >= self.cap:
            time.sleep(max(0, 60 - (now - self.times[0]) + 0.1))
        self.times.append(time.time())
        self.count += 1


def http_get(url: str, params: dict | None, limiter: RateLimiter, desc: str):
    for attempt in range(1, 4):
        try:
            limiter.wait()
            r = requests.get(url, headers=HEADERS, params=params, timeout=30)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            retry = isinstance(e, (requests.ConnectionError, requests.Timeout)) or (
                code in (429,) or (code is not None and 500 <= code < 600)
            )
            if attempt == 3 or not retry:
                raise
            time.sleep(2**attempt)
            print(f"  重试 {desc} ({code})")


def fetch_exchange_list(limiter: RateLimiter) -> list[dict]:
    data = http_get(EXCHANGES_LIST_URL, {"status": "active"}, limiter, "list").json()
    return [x for x in data if isinstance(x, dict) and x.get("id")]


def uniswap_ids(exchanges: list[dict]) -> list[str]:
    out = []
    for x in exchanges:
        eid, name = str(x.get("id", "")), str(x.get("name", ""))
        if UNISWAP_ID_RE.search(eid) and not UNISWAP_SKIP_NAME_RE.search(name):
            out.append(eid)
    return sorted(set(out))


def cg_ids_for_column(col: str, exchanges: list[dict]) -> list[str]:
    if col == UNISWAP_COL:
        return uniswap_ids(exchanges)
    return [col]


def pick_days(need_days: int) -> int:
    d = max(MIN_API_DAYS, min(need_days, MAX_DAYS))
    for opt in SUPPORTED_DAYS:
        if d <= opt:
            return opt
    return MAX_DAYS


def fetch_volume_btc(exchange_id: str, days: int, limiter: RateLimiter) -> dict[str, float]:
    url = f"{EXCHANGES_BASE_URL}/{exchange_id}/volume_chart"
    try:
        raw = http_get(url, {"days": str(days)}, limiter, exchange_id).json()
    except requests.RequestException as e:
        print(f"  [FAIL] {exchange_id}: {e}")
        return {}
    if not isinstance(raw, list):
        return {}
    daily: dict[str, float] = defaultdict(float)
    for pt in raw:
        if isinstance(pt, list) and len(pt) >= 2:
            ts = int(pt[0])
            day = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            daily[day] += float(pt[1])
    return dict(daily)


def fetch_column(col: str, days: int, exchanges: list[dict], limiter: RateLimiter) -> dict[str, float]:
    merged: dict[str, float] = defaultdict(float)
    for eid in cg_ids_for_column(col, exchanges):
        for day, vol in fetch_volume_btc(eid, days, limiter).items():
            merged[day] += vol
    return dict(merged)


def read_csv(path: Path) -> tuple[dict[str, dict[str, float]], datetime.date | None]:
    if not path.exists():
        return {}, None
    df = pd.read_csv(path, encoding="utf-8-sig")
    if df.empty:
        return {}, None
    dcol = df.columns[0]
    df = df.rename(columns={dcol: DATE_COL})
    df[DATE_COL] = pd.to_datetime(df[DATE_COL], errors="coerce").dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=[DATE_COL])
    data: dict[str, dict[str, float]] = {}
    for _, row in df.iterrows():
        day = str(row[DATE_COL])
        slot = {}
        for col in COLUMNS:
            if col == TOTAL_COL:
                continue
            v = pd.to_numeric(row.get(col), errors="coerce")
            if pd.notna(v):
                slot[col] = float(v)
        if slot:
            data[day] = slot
    if not data:
        return {}, None
    last = datetime.strptime(max(data.keys()), "%Y-%m-%d").date()
    return data, last


def write_csv(path: Path, data: dict[str, dict[str, float]]) -> int:
    if not data:
        return 0
    data_cols = [c for c in COLUMNS if c != TOTAL_COL]
    for slot in data.values():
        vals = [slot[c] for c in data_cols if c in slot]
        if vals:
            slot[TOTAL_COL] = sum(vals)
    dmin = datetime.strptime(min(data.keys()), "%Y-%m-%d").date()
    dmax = datetime.strptime(max(data.keys()), "%Y-%m-%d").date()
    rows = []
    for i in range((dmax - dmin).days + 1):
        key = (dmin + timedelta(days=i)).strftime("%Y-%m-%d")
        row = {DATE_COL: key}
        row.update(data.get(key, {}))
        rows.append(row)
    out = pd.DataFrame(rows)
    for col in COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    out[[DATE_COL] + COLUMNS].to_csv(path, index=False, encoding="utf-8-sig")
    return len(out)


def parse_args():
    p = argparse.ArgumentParser(description="下载 CoinGecko 成交量(BTC)到 major_vol_切勿删除.csv")
    p.add_argument("--mode", choices=("append", "update"), default="append",
                   help="无文件或首次=拉一年；append=补未下载；update=覆盖近一年")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    end = datetime.now(timezone.utc).date()
    data, last = read_csv(TARGET_CSV)
    limiter = RateLimiter(MAX_REQUESTS_PER_MINUTE)
    exlist = fetch_exchange_list(limiter)

    if args.mode == "update" or last is None:
        start = end - timedelta(days=MAX_DAYS - 1)
        api_days = MAX_DAYS
        label = "新建/更新近一年" if last is None else "update 覆盖近一年"
    else:
        start = last + timedelta(days=1)
        if start > end:
            print("已是最新")
            return
        need = (end - start).days + 1
        api_days = pick_days(need + 2)
        label = f"append 补 {start} ~ {end}"

    print(f"{label} | API days={api_days} | 窗口 {start} ~ {end}")

    n = 0
    for i, col in enumerate(DOWNLOAD_COLUMNS, 1):
        print(f"[{i}/{len(DOWNLOAD_COLUMNS)}] {col}")
        daily = fetch_column(col, api_days, exlist, limiter)
        for day, vol in daily.items():
            d = datetime.strptime(day, "%Y-%m-%d").date()
            # API 可能多拉窗口外日期：append 舍弃，update 仅覆盖 [start, end]。
            if d < start or d > end:
                continue
            if args.mode == "append" and col in data.get(day, {}):
                continue
            data.setdefault(day, {})[col] = vol
            n += 1

    if not data:
        print("无数据")
        return

    write_csv(TARGET_CSV, data)
    print(f"写入 {TARGET_CSV}")
    print(f"区间 {min(data)} ~ {max(data)} | 本次 {n} 格 | 请求 {limiter.count}")


if __name__ == "__main__":
    main()
