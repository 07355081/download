from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import numpy as np


from _paths import (
    ASSET_WIDE_CSV,
    BTC_DAILY_VWAP_CSV,
    CACHE_DIR,
    FACTOR_DAILY_CSV,
    MAJOR_VOL_CSV,
    PIPELINE_XLSX,
    ensure_cache_layout,
)
from _closed_months import date_in_closed_month, list_closed_months, period_is_closed

BACKUP_DIR = CACHE_DIR / "backups"
BACKUP_KEEP = 14
DAILY_FREEZE_SHEETS = (
    "现货",
    "合约",
    "现货usd",
    "合约usd",
    "现货usd调整",
    "合约usd调整",
)
MONTHLY_FREEZE_SHEETS = ("现货月度", "合约月度")

OUTPUT_XLSX = PIPELINE_XLSX
EMA_SPAN = 14
K_DEFAULT = 2.0
EARLY_FACTOR_ANCHOR_WINDOW = 90
EARLY_FACTOR_TRANSITION_WINDOW = 45
EARLY_FACTOR_FLOOR = 0.15
# 异常平滑参数：用 ±30 天的居中滚动中位数（仅对 > 0 的值取中位数）作为基线
ANOMALY_WINDOW = 61
ANOMALY_SPIKE_RATIO = 5.0
ANOMALY_DIP_RATIO = 5.0
ANOMALY_DIP_SUPPORT_WINDOW = 5
ANOMALY_DIP_MIN_SUPPORT = 2
ANOMALY_MIN_NEIGHBORS = 5
ANOMALY_MAX_PASSES = 10
EXTRA_HALF_FACTOR_KEYWORDS = ("uniswap", "pancake", "balancer", "curve", "sushiswap", "raydium")
MIN_VALID_DAYS_PER_MONTH = 28
EXCHANGE_RENAMES = {
    "Binance CEX": "Binance",
    "Crypto-com": "Crypto.com",
}

ROOT_AGGREGATIONS = [
    ("balancer", re.compile(r"(?i)\bbalancer")),
    ("curve", re.compile(r"(?i)\bcurve")),
    ("dydx", re.compile(r"(?i)\bdydx")),
    ("gmx", re.compile(r"(?i)\bgmx")),
    ("orca", re.compile(r"(?i)\borca")),
    ("pancakeswap", re.compile(r"(?i)\bpancakeswap")),
    ("raydium", re.compile(r"(?i)\braydium")),
    ("sushiswap", re.compile(r"(?i)\bsushiswap")),
    ("uniswap", re.compile(r"(?i)\buniswap")),
]


CATEGORY_OVERRIDES = {
    "okex": "现货",
    "okex_swap": "合约",
    "binance": "现货",
    "bitfinex": "现货",
    "gdax": "现货",
    "gate": "现货",
    "huobi": "现货",
    "kraken": "现货",
    "kucoin": "现货",
    "upbit": "现货",
    "mxc": "现货",
    "bitmex": "合约",
    "deribit": "合约",
    "gate_futures": "合约",
    "huobi_dm": "合约",
    "kraken_futures": "合约",
    "bybit": "合约",
    "binance_futures": "合约",
    "kumex": "合约",
    "bitfinex_futures": "合约",
    "dydx_perpetual_l1": "合约",
    "bitget": "现货",
    "mxc_futures": "合约",
    "crypto_com": "现货",
    "bitget_futures": "合约",
    "perpetual_protocol": "合约",
    "dydx_perpetual": "合约",
    "crypto_com_futures": "合约",
    "bybit_spot": "现货",
    "bitmex_spot": "现货",
    "deribit_spot": "现货",
    "hyperliquid": "合约",
    "coinbase_international_derivatives": "合约",
    "aevo": "合约",
    "dydx_chain": "合约",
    "hyperliquid-spot": "现货",
    "gmx-perpetuals-v2-arbitrum": "合约",
    "aster": "合约",
    "gmx-perpetuals-v2-avalanche": "合约",
    "aster-spot": "现货",
    "gmx-perpetuals-v2-botanix": "合约",
}

DERIVATIVE_IDS = {k for k, v in CATEGORY_OVERRIDES.items() if v == "合约"}

SPOT_NAME_MAP = {
    "binance": "Binance",
    "bitfinex": "Bitfinex",
    "bitget": "Bitget",
    "bybit_spot": "Bybit",
    "crypto_com": "Crypto.com",
    "deribit_spot": "Deribit",
    "gate": "Gate",
    "huobi": "HTX",
    "kucoin": "KuCoin",
    "mxc": "MEXC",
    "okex": "OKX",
    "gdax": "Coinbase",
    "kraken": "Kraken",
    "hyperliquid-spot": "Hyperliquid",
    "upbit": "Upbit",
    "uniswap": "Uniswap",
}

FUTURES_NAME_MAP = {
    "binance_futures": "Binance",
    "bitfinex_futures": "Bitfinex",
    "bitget_futures": "Bitget",
    "bybit": "Bybit",
    "crypto_com_futures": "Crypto.com",
    "deribit": "Deribit",
    "gate_futures": "Gate",
    "huobi_dm": "HTX",
    "kumex": "KuCoin",
    "mxc_futures": "MEXC",
    "okex_swap": "OKX",
    "coinbase_international_derivatives": "Coinbase",
    "kraken_futures": "Kraken",
    "hyperliquid": "Hyperliquid",
}

SPOT_KEEP_EXCHANGES = [
    "Binance",
    "Bitfinex",
    "Bitget",
    "Bybit",
    "Crypto.com",
    "Deribit",
    "Gate",
    "HTX",
    "KuCoin",
    "MEXC",
    "OKX",
    "Coinbase",
    "Kraken",
    "Upbit",
    "Hyperliquid",
    "Uniswap",
]

FUTURES_KEEP_EXCHANGES = [
    "Binance",
    "Bitfinex",
    "Bitget",
    "Bybit",
    "Crypto.com",
    "Deribit",
    "Gate",
    "HTX",
    "KuCoin",
    "MEXC",
    "OKX",
    "Coinbase",
    "Kraken",
    "Hyperliquid",
]

CORE_EXCHANGES = [
    "Binance",
    "Bitfinex",
    "Bitget",
    "Bybit",
    "Crypto.com",
    "Deribit",
    "Gate",
    "HTX",
    "KuCoin",
    "MEXC",
    "OKX",
]


def read_csv_with_fallback(path: Path) -> pd.DataFrame:
    for enc in ("utf-8", "utf-8-sig", "gb18030", "gbk", "cp936"):
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path)


def canonicalize_exchange_columns(df: pd.DataFrame, date_column: str = "date") -> pd.DataFrame:
    """将旧版交易所名规范到当前命名（兼容历史 CSV/因子列名）。"""
    out = df.copy()
    for old_name, new_name in EXCHANGE_RENAMES.items():
        if old_name not in out.columns:
            continue
        if new_name in out.columns:
            old_series = pd.to_numeric(out[old_name], errors="coerce")
            new_series = pd.to_numeric(out[new_name], errors="coerce")
            out[new_name] = old_series.combine_first(new_series)
            out = out.drop(columns=[old_name], errors="ignore")
        else:
            out = out.rename(columns={old_name: new_name})
    ordered_cols = [c for c in out.columns if c != date_column]
    return out[[date_column] + ordered_cols] if date_column in out.columns else out


def load_assets_wide_df(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"{path} 不存在，请先运行 fetch_cex_total_assets_merged.py")
    df = read_csv_with_fallback(path)
    if "date" not in df.columns:
        raise ValueError("资产汇总表缺少 date 列")
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return canonicalize_exchange_columns(df, date_column="date")


def aggregate_roots(df: pd.DataFrame, date_column: str) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    used_columns = set()
    root_map: dict[str, list[str]] = {}
    out = df.copy()
    for root_name, pattern in ROOT_AGGREGATIONS:
        candidates = [
            col for col in out.columns
            if col != date_column and col not in used_columns and pattern.search(col)
        ]
        if not candidates:
            continue
        out[root_name] = out[candidates].sum(axis=1, skipna=True)
        # 避免原列名与 root_name 相同被删除
        used_columns.update([c for c in candidates if c != root_name])
        root_map[root_name] = candidates
    out = out.drop(columns=list(used_columns), errors="ignore")
    remaining_columns = [col for col in out.columns if col != date_column and col not in root_map]
    out = out[[date_column] + list(root_map.keys()) + remaining_columns]
    return out, root_map


def build_classification_map(df: pd.DataFrame, root_map: dict[str, list[str]], date_column: str) -> dict[str, str]:
    m: dict[str, str] = {}
    for col in df.columns:
        if col == date_column:
            continue
        source_ids = root_map.get(col, [col])
        m[col] = "合约" if any(i in DERIVATIVE_IDS for i in source_ids) else "现货"
    return m


def split_by_category(df: pd.DataFrame, class_map: dict[str, str], date_column: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    spot_cols = [date_column] + [c for c in df.columns if c != date_column and class_map.get(c) == "现货"]
    futures_cols = [date_column] + [c for c in df.columns if c != date_column and class_map.get(c) == "合约"]
    return df[spot_cols].copy(), df[futures_cols].copy()


def map_volume_wide(vol_df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    keep_cols = [vol_df.columns[0]] + [c for c in mapping if c in vol_df.columns]
    out = vol_df[keep_cols].copy().rename(columns={vol_df.columns[0]: "date", **mapping})
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    value_df = out.drop(columns=["date"]).apply(pd.to_numeric, errors="coerce")
    # 若多个源列映射到同一交易所，合并求和
    value_df = value_df.T.groupby(level=0).sum(min_count=1).T
    return pd.concat([out["date"], value_df], axis=1)


def compute_core_factors(
    spot_usd_df: pd.DataFrame,
    assets_wide_df: pd.DataFrame,
    k_default: float = K_DEFAULT,
) -> pd.DataFrame:
    exchanges = [c for c in CORE_EXCHANGES if c in spot_usd_df.columns and c in assets_wide_df.columns]
    if "Binance" not in exchanges:
        raise ValueError("Binance data missing, cannot build factors")
    merged = spot_usd_df[["date"] + exchanges].merge(
        assets_wide_df[["date"] + exchanges], on="date", how="inner", suffixes=("_vol", "_asset")
    )
    out = pd.DataFrame({"date": merged["date"]})
    b_vol = pd.to_numeric(merged["Binance_vol"], errors="coerce")
    b_asset = pd.to_numeric(merged["Binance_asset"], errors="coerce")
    with np.errstate(divide="ignore", invalid="ignore"):
        b_turnover = b_vol / b_asset
    b_turnover_smooth = b_turnover.ewm(span=EMA_SPAN, adjust=False, min_periods=1).mean()
    for ex in exchanges:
        vol = pd.to_numeric(merged[f"{ex}_vol"], errors="coerce")
        asset = pd.to_numeric(merged[f"{ex}_asset"], errors="coerce")
        cap = asset * k_default * b_turnover_smooth
        adj = np.minimum(vol, cap)
        with np.errstate(divide="ignore", invalid="ignore"):
            f = adj / vol
        f = pd.Series(f).replace([np.inf, -np.inf], np.nan)
        if ex == "Binance":
            f = pd.Series(np.where(vol.notna(), 1.0, np.nan), index=vol.index)
        out[ex] = f
    return out


def extend_factor_wide(factor_core_wide: pd.DataFrame, spot_usd_df: pd.DataFrame) -> pd.DataFrame:
    f = factor_core_wide.copy()
    ok = pd.to_numeric(f.get("OKX"), errors="coerce")
    bybit = pd.to_numeric(f.get("Bybit"), errors="coerce")
    gate = pd.to_numeric(f.get("Gate"), errors="coerce")
    mexc = pd.to_numeric(f.get("MEXC"), errors="coerce")
    htx = pd.to_numeric(f.get("HTX"), errors="coerce")
    proxy_ok_bybit = pd.concat([ok, bybit], axis=1).mean(axis=1, skipna=True)
    proxy_gate_mexc_htx = pd.concat([gate, mexc, htx], axis=1).mean(axis=1, skipna=True)
    if "Kraken" in spot_usd_df.columns:
        f["Kraken"] = proxy_ok_bybit
    if "Coinbase" in spot_usd_df.columns:
        f["Coinbase"] = proxy_ok_bybit
    if "Upbit" in spot_usd_df.columns:
        f["Upbit"] = proxy_gate_mexc_htx
    if "Hyperliquid" in spot_usd_df.columns:
        f["Hyperliquid"] = 1.0
    if "Uniswap" in spot_usd_df.columns:
        f["Uniswap"] = 1.0
    keep_cols = ["date"] + [c for c in SPOT_KEEP_EXCHANGES if c in f.columns]
    return f[keep_cols]


def load_btc_vwap_map(path: Path) -> dict[str, float]:
    df = read_csv_with_fallback(path)
    if "date" not in df.columns or "vwap_usd" not in df.columns:
        raise ValueError("btc_daily_vwap.csv 需包含 date 和 vwap_usd 列")
    df = df.dropna(subset=["date", "vwap_usd"]).copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return dict(zip(df["date"], pd.to_numeric(df["vwap_usd"], errors="coerce")))


def convert_btc_to_usd(df: pd.DataFrame, date_column: str, vwap_map: dict[str, float]) -> pd.DataFrame:
    out = df.copy()
    out[date_column] = pd.to_datetime(out[date_column], errors="coerce").dt.strftime("%Y-%m-%d")
    fx = out[date_column].map(vwap_map).astype(float)
    for col in out.columns:
        if col == date_column:
            continue
        out[col] = pd.to_numeric(out[col], errors="coerce") * fx
    return out


def smooth_volume_anomalies_wide(
    df: pd.DataFrame,
    date_column: str = "date",
    window: int = ANOMALY_WINDOW,
    spike_ratio: float = ANOMALY_SPIKE_RATIO,
    dip_ratio: float = ANOMALY_DIP_RATIO,
    dip_support_window: int = ANOMALY_DIP_SUPPORT_WINDOW,
    dip_min_support: int = ANOMALY_DIP_MIN_SUPPORT,
    min_neighbors: int = ANOMALY_MIN_NEIGHBORS,
    max_passes: int = ANOMALY_MAX_PASSES,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """对成交量异常值做稳健平滑（仅修改内存数据，不改原始 CSV）。

    针对源文件常见的三类异常：
    1) 长期为 0 的中段（数据缺失/未补齐）；
    2) 单日或连续多日的异常高值（高出周边 5 倍以上）。
    3) 单日异常低值（低于周边 1/5），常见于某天采集故障导致比值失真。

    实现要点：
    - 以当前日为中心，取宽度 ``window``（默认 61 天，即 ±30 天）的居中滚动中位数作为基线；
      仅取窗口内 > 0 的值进入中位数计算，避免 0 值压低基线。
    - 异常判定：
      - 当日为 NaN/0，替换为基线；
      - 当日 ≥ 基线 × ``spike_ratio``（高值尖峰）时，替换为基线；
      - 当日 > 0 且 ≤ 基线 / ``dip_ratio``（异常低值）且邻近 ``dip_support_window``
        天内至少有 ``dip_min_support`` 天不低于该阈值时，替换为基线。
        该“邻居支撑”条件避免把真实持续下行趋势误判为异常。
    - 多轮迭代（最多 ``max_passes`` 次）：连续多日异常一次未必清干净，
      被替换的值会让下一轮基线更稳健，直至无新增修改时收敛。即使连续 10+ 天为
      0 或为高值，相对 60 天窗口仍是少数，中位数不会被污染。
    - 跳过该列首个 > 0 值之前的索引（视作"交易所未上线"），避免把占位 0 当成异常。
    - 至少需要 ``min_neighbors`` 个有效邻居才会做出替换，数据稀疏列保持不动。
    """
    out = df.copy()
    if date_column in out.columns:
        out[date_column] = pd.to_datetime(out[date_column], errors="coerce").dt.strftime("%Y-%m-%d")
        out = out.sort_values(date_column).reset_index(drop=True)

    stats = {"missing_smoothed": 0, "zero_smoothed": 0, "spike_smoothed": 0, "dip_smoothed": 0}
    value_cols = [c for c in out.columns if c != date_column]
    n = len(out)
    if n == 0 or not value_cols:
        return out, stats

    for col in value_cols:
        series = pd.to_numeric(out[col], errors="coerce").astype(float)
        finite_pos_mask = series.notna() & (series > 0)
        if not finite_pos_mask.any():
            continue
        first_valid_idx = int(np.argmax(finite_pos_mask.to_numpy()))
        eligible = pd.Series(np.arange(n) >= first_valid_idx, index=series.index)

        corrected = series.copy()
        for _ in range(max_passes):
            pos_only = corrected.where(corrected > 0)
            baseline = pos_only.rolling(window, center=True, min_periods=min_neighbors).median()
            base_ok = baseline.notna() & (baseline > 0) & eligible

            is_missing = corrected.isna() & base_ok
            is_zero = (corrected == 0) & base_ok
            is_spike = (corrected >= baseline * spike_ratio) & base_ok
            # 仅修“孤立/短段低值”：候选低值 + 邻居支撑，避免误改真实趋势切换。
            dip_cutoff = baseline / dip_ratio
            is_dip_candidate = (corrected > 0) & (corrected <= dip_cutoff) & base_ok
            near_normal = (corrected > dip_cutoff) & base_ok
            support_count = near_normal.rolling(dip_support_window, center=True, min_periods=1).sum()
            support_count = support_count - near_normal.astype(int)
            has_support = support_count >= dip_min_support
            is_dip = is_dip_candidate & has_support

            needs = is_missing | is_zero | is_spike | is_dip
            if not needs.any():
                break

            stats["missing_smoothed"] += int(is_missing.sum())
            stats["zero_smoothed"] += int(is_zero.sum())
            stats["spike_smoothed"] += int(is_spike.sum())
            stats["dip_smoothed"] += int(is_dip.sum())
            corrected = corrected.where(~needs, baseline)

        out[col] = corrected
    return out, stats


def load_factor_df(path: Path) -> pd.DataFrame:
    f = read_csv_with_fallback(path)
    if "date" not in f.columns:
        raise ValueError("cex_dynamic_factors_daily.csv 需包含 date 列")
    f["date"] = pd.to_datetime(f["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return canonicalize_exchange_columns(f, date_column="date")


def backfill_early_factors(
    factor_df: pd.DataFrame,
    anchor_window: int = EARLY_FACTOR_ANCHOR_WINDOW,
    transition_window: int = EARLY_FACTOR_TRANSITION_WINDOW,
    floor: float = EARLY_FACTOR_FLOOR,
) -> pd.DataFrame:
    """对早期缺失因子做回填 + 平滑过渡 + 下限约束。

    - 对每个交易所列：取首个有效因子位置 T0，以及 [T0, T0+anchor_window) 内
      有效因子的中位数 f_early 作为早期锚。
    - 在 [T0 - transition_window, T0 + transition_window] 区间做线性混合：
      f_final = w * f_early + (1 - w) * f_real_filled，其中 w 从 1 线性降到 0；
      T0 之前缺失值先用 f_real(T0) 占位，避免出现 NaN 参与混合。
    - 最终对所有非空因子施加下限 floor 与上限 1.0。
    """
    out = factor_df.copy()
    if "date" not in out.columns:
        return out
    out = out.sort_values("date").reset_index(drop=True)
    n = len(out)
    if n == 0:
        return out

    for col in out.columns:
        if col == "date":
            continue
        f = pd.to_numeric(out[col], errors="coerce").reset_index(drop=True)
        valid_pos = np.where(f.notna().to_numpy())[0]
        if len(valid_pos) == 0:
            continue
        t0 = int(valid_pos[0])
        anchor_pos = valid_pos[: min(anchor_window, len(valid_pos))]
        f_early = float(np.nanmedian(f.iloc[anchor_pos].to_numpy()))
        if not np.isfinite(f_early):
            continue
        f_target = float(f.iloc[t0])

        # T0 之前缺失值用 f_target 占位，避免参与混合时变成 NaN
        before_t0 = pd.Series(np.arange(n) < t0, index=f.index)
        f_filled = f.where(~before_t0, f_target)

        # 过渡权重：[T0-W, T0+W] 区间从 1 线性降到 0
        if transition_window > 0:
            idx_arr = np.arange(n)
            w = np.clip((t0 + transition_window - idx_arr) / (2 * transition_window), 0.0, 1.0)
        else:
            w = (np.arange(n) < t0).astype(float)
        w = pd.Series(w, index=f.index)

        f_final = w * f_early + (1.0 - w) * f_filled
        f_final = f_final.clip(lower=floor, upper=1.0)
        out[col] = f_final.to_numpy()
    return out


def reindex_factor_to_dates(factor_df: pd.DataFrame, all_dates: list[str]) -> pd.DataFrame:
    """把因子表扩展到给定的完整日期集合（早于因子起点的日期会变成 NaN，由 backfill 处理）。"""
    if "date" not in factor_df.columns:
        return factor_df
    out = factor_df.set_index("date").reindex(sorted(set(all_dates))).rename_axis("date").reset_index()
    return out


def apply_factors_to_wide(raw_wide: pd.DataFrame, factor_wide: pd.DataFrame, keep_exchanges: list[str]) -> pd.DataFrame:
    # 日期在因子表不存在，或某交易所当日因子缺失（常见于资产缺失）时，保留原始成交量。
    factor_dates = set(factor_wide["date"].dropna().astype(str)) if "date" in factor_wide.columns else set()
    merged = raw_wide.merge(factor_wide, on="date", how="left", suffixes=("_raw", "_factor"))
    has_factor_date = merged["date"].astype(str).isin(factor_dates)
    out = pd.DataFrame({"date": merged["date"]})
    for ex in keep_exchanges:
        raw_col = f"{ex}_raw"
        factor_col = f"{ex}_factor"
        if raw_col not in merged.columns:
            continue
        vol_raw = pd.to_numeric(merged[raw_col], errors="coerce")
        if factor_col in merged.columns:
            f = pd.to_numeric(merged[factor_col], errors="coerce")
        else:
            f = pd.Series(pd.NA, index=vol_raw.index, dtype="float64")
        # 只有因子可用时才调整；其余情况回退原始 usd。
        apply_mask = has_factor_date & f.notna()
        adjusted = vol_raw * f
        out[ex] = adjusted.where(apply_mask, vol_raw)
    return out


def apply_protocol_extra_half_factor(factor_wide: pd.DataFrame) -> pd.DataFrame:
    out = factor_wide.copy()
    for col in out.columns:
        if col == "date":
            continue
        col_lower = str(col).lower()
        if any(k in col_lower for k in EXTRA_HALF_FACTOR_KEYWORDS):
            out[col] = pd.to_numeric(out[col], errors="coerce") * 0.5
    return out


def count_valid_days_per_month(df: pd.DataFrame, date_column: str) -> pd.Series:
    """每月至少一个交易所成交量 > 0 的日历日数量（0 占位不计入）。"""
    tmp = df.copy()
    tmp[date_column] = pd.to_datetime(tmp[date_column], errors="coerce")
    value_cols = [c for c in tmp.columns if c != date_column]
    numeric = tmp[value_cols].apply(pd.to_numeric, errors="coerce")
    valid_row = (numeric > 0).any(axis=1) & tmp[date_column].notna()
    tmp = tmp.loc[valid_row]
    if tmp.empty:
        return pd.Series(dtype=int)
    return tmp.groupby(tmp[date_column].dt.to_period("M")).size()


def monthly_summary(
    df: pd.DataFrame,
    date_column: str,
    *,
    min_valid_days: int = MIN_VALID_DAYS_PER_MONTH,
) -> pd.DataFrame:
    tmp = df.copy()
    tmp[date_column] = pd.to_datetime(tmp[date_column], errors="coerce")
    value_cols = [c for c in tmp.columns if c != date_column]

    day_counts = count_valid_days_per_month(df, date_column)
    complete_months = day_counts[day_counts >= min_valid_days].index
    if len(complete_months) == 0:
        out = pd.DataFrame(columns=["交易所"])
        return out

    month_period = tmp[date_column].dt.to_period("M")
    tmp = tmp.loc[month_period.isin(complete_months)].copy()
    grouped = tmp.groupby(tmp[date_column].dt.to_period("M"))[value_cols].sum(min_count=1)
    skipped = sorted({str(m) for m in day_counts.index} - {str(m) for m in complete_months})
    if skipped:
        print(f"  月度跳过（有效日 < {min_valid_days}）: {', '.join(skipped)}")

    grouped.index = grouped.index.astype(str)
    out = grouped.T
    out.index.name = "交易所"
    return out.reset_index()


def backup_pipeline_xlsx(path: Path) -> Path | None:
    """Copy existing pipeline xlsx before overwrite. Returns backup path or None."""
    if not path.is_file():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = BACKUP_DIR / f"cex_volume_pipeline_unified_{stamp}.xlsx"
    shutil.copy2(path, dest)
    backups = sorted(BACKUP_DIR.glob("cex_volume_pipeline_unified_*.xlsx"))
    for old in backups[:-BACKUP_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass
    print(f"[freeze] backup → {dest}")
    return dest


def _normalize_daily_sheet(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    date_col = out.columns[0]
    out = out.rename(columns={date_col: "date"})
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return out.dropna(subset=["date"])


def freeze_daily_closed_months(new_df: pd.DataFrame, old_df: pd.DataFrame | None, closed: set[str]) -> pd.DataFrame:
    """Keep old daily rows whose date falls in a closed month."""
    if not closed or old_df is None or old_df.empty:
        return new_df
    new_n = _normalize_daily_sheet(new_df)
    old_n = _normalize_daily_sheet(old_df)
    old_idx = old_n.set_index("date")
    rows: list[dict] = []
    frozen = 0
    for _, row in new_n.iterrows():
        day = str(row["date"])
        if date_in_closed_month(day, closed) and day in old_idx.index:
            kept = old_idx.loc[day]
            if isinstance(kept, pd.DataFrame):
                kept = kept.iloc[0]
            merged = {"date": day}
            for col in new_n.columns:
                if col == "date":
                    continue
                merged[col] = kept[col] if col in kept.index else row[col]
            rows.append(merged)
            frozen += 1
        else:
            rows.append(row.to_dict())
    # Preserve closed-month dates that exist only in old file
    new_days = set(new_n["date"].astype(str))
    for day, kept in old_idx.iterrows():
        day_s = str(day)
        if day_s in new_days or not date_in_closed_month(day_s, closed):
            continue
        merged = {"date": day_s}
        for col in new_n.columns:
            if col == "date":
                continue
            merged[col] = kept[col] if col in kept.index else pd.NA
        rows.append(merged)
        frozen += 1
    out = pd.DataFrame(rows)
    out = out.sort_values("date").reset_index(drop=True)
    # Restore original first-column name if needed
    first = new_df.columns[0]
    if first != "date":
        out = out.rename(columns={"date": first})
    print(f"[freeze] daily kept closed rows≈{frozen}")
    return out


def freeze_monthly_closed_months(
    new_df: pd.DataFrame, old_df: pd.DataFrame | None, closed: set[str]
) -> pd.DataFrame:
    """Keep old monthly columns for closed YYYY-MM."""
    if not closed or old_df is None or old_df.empty:
        return new_df
    ex_col = new_df.columns[0]
    out = new_df.copy()
    old = old_df.copy()
    old_ex = old.columns[0]
    old = old.rename(columns={old_ex: ex_col})
    old_by_ex = old.set_index(ex_col)
    frozen_cols = 0
    for col in list(out.columns):
        if col == ex_col:
            continue
        key = str(col)
        # excel may give Timestamp columns
        try:
            key = pd.Timestamp(col).strftime("%Y-%m")
        except (ValueError, TypeError):
            key = str(col)[:7]
        if not period_is_closed(key, closed):
            continue
        # find matching old column
        old_col = None
        for c in old.columns:
            if c == ex_col:
                continue
            try:
                ck = pd.Timestamp(c).strftime("%Y-%m")
            except (ValueError, TypeError):
                ck = str(c)[:7]
            if ck == key:
                old_col = c
                break
        if old_col is None:
            continue
        for i, ex in enumerate(out[ex_col].tolist()):
            if pd.isna(ex) or ex not in old_by_ex.index:
                continue
            out.at[i, col] = old_by_ex.at[ex, old_col]
        frozen_cols += 1
    print(f"[freeze] monthly kept closed cols={frozen_cols}")
    return out


def load_old_pipeline_sheets(path: Path) -> dict[str, pd.DataFrame]:
    if not path.is_file():
        return {}
    try:
        xl = pd.ExcelFile(path)
    except Exception as e:
        raise SystemExit(f"[freeze] cannot read existing pipeline for merge: {path} ({e})") from e
    out: dict[str, pd.DataFrame] = {}
    for name in list(DAILY_FREEZE_SHEETS) + list(MONTHLY_FREEZE_SHEETS):
        if name in xl.sheet_names:
            out[name] = pd.read_excel(xl, sheet_name=name)
    return out


def apply_closed_month_freeze(
    sheets: dict[str, pd.DataFrame],
    old_sheets: dict[str, pd.DataFrame],
    closed: set[str],
) -> dict[str, pd.DataFrame]:
    if not closed:
        print("[freeze] no closed months (no monthly_report_YYYY-MM.xlsx); write as computed")
        return sheets
    if not old_sheets:
        raise SystemExit(
            "[freeze] closed months exist but prior pipeline xlsx missing/unreadable; "
            "refusing to overwrite. Restore cache/cex_volume_pipeline_unified.xlsx or backups/."
        )
    out = dict(sheets)
    for name in DAILY_FREEZE_SHEETS:
        if name in out:
            out[name] = freeze_daily_closed_months(out[name], old_sheets.get(name), closed)
    for name in MONTHLY_FREEZE_SHEETS:
        if name in out:
            out[name] = freeze_monthly_closed_months(out[name], old_sheets.get(name), closed)
    print(f"[freeze] closed months: {', '.join(sorted(closed))}")
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按资产约束调整 CEX 成交量")
    parser.add_argument(
        "--k",
        type=float,
        default=K_DEFAULT,
        help=f"削峰容忍系数，默认 {K_DEFAULT}",
    )
    return parser.parse_args()


def main() -> None:
    ensure_cache_layout()
    args = parse_args()
    if not np.isfinite(args.k) or args.k <= 0:
        raise ValueError(f"--k 必须是正数，当前值: {args.k}")
    k_value = float(args.k)

    if not MAJOR_VOL_CSV.exists():
        raise FileNotFoundError(f"{MAJOR_VOL_CSV} 不存在")
    if not BTC_DAILY_VWAP_CSV.exists():
        raise FileNotFoundError(f"{BTC_DAILY_VWAP_CSV} 不存在")
    if not FACTOR_DAILY_CSV.exists():
        print(f"[INFO] {FACTOR_DAILY_CSV} 不存在，将自动重建")
    if not ASSET_WIDE_CSV.exists():
        raise FileNotFoundError(f"{ASSET_WIDE_CSV} 不存在，请先执行 fetch_cex_total_assets_merged.py")

    raw_df = read_csv_with_fallback(MAJOR_VOL_CSV)
    date_col = raw_df.columns[0]
    raw_df[date_col] = pd.to_datetime(raw_df[date_col], errors="coerce")
    raw_df = raw_df.dropna(subset=[date_col]).copy()
    raw_df[date_col] = raw_df[date_col].dt.strftime("%Y-%m-%d")

    aggregated_df, root_map = aggregate_roots(raw_df, date_col)
    class_map = build_classification_map(aggregated_df, root_map, date_col)
    spot_df_raw, futures_df_raw = split_by_category(aggregated_df, class_map, date_col)

    # 严格复刻旧版列筛选与命名映射
    spot_df = map_volume_wide(spot_df_raw, SPOT_NAME_MAP)
    futures_df = map_volume_wide(futures_df_raw, FUTURES_NAME_MAP)

    # 仅保留旧版目标交易所列
    spot_cols = ["date"] + [c for c in SPOT_KEEP_EXCHANGES if c in spot_df.columns]
    futures_cols = ["date"] + [c for c in FUTURES_KEEP_EXCHANGES if c in futures_df.columns]
    spot_df = spot_df[spot_cols]
    futures_df = futures_df[futures_cols]

    # 在 BTC→USD 转换前对成交量做异常平滑，保证现货/合约的 BTC、USD、
    # 调整后、月度等所有下游口径以及因子计算都基于干净数据。
    spot_df, spot_smooth_stats = smooth_volume_anomalies_wide(spot_df, date_column="date")
    futures_df, futures_smooth_stats = smooth_volume_anomalies_wide(futures_df, date_column="date")
    print(f"[INFO] 现货异常平滑统计: {spot_smooth_stats}")
    print(f"[INFO] 合约异常平滑统计: {futures_smooth_stats}")

    vwap_map = load_btc_vwap_map(BTC_DAILY_VWAP_CSV)
    spot_usd_df = convert_btc_to_usd(spot_df, "date", vwap_map)
    futures_usd_df = convert_btc_to_usd(futures_df, "date", vwap_map)

    # 读取资产汇总表并重算惩罚因子
    assets_wide_df = load_assets_wide_df(ASSET_WIDE_CSV)
    factor_core_df = compute_core_factors(spot_usd_df, assets_wide_df, k_default=k_value)
    factor_df = extend_factor_wide(factor_core_df, spot_usd_df)
    factor_df = apply_protocol_extra_half_factor(factor_df)
    factor_df.to_csv(FACTOR_DAILY_CSV, index=False, encoding="utf-8-sig")
    factor_df = load_factor_df(FACTOR_DAILY_CSV)

    # 把因子扩展到原始 USD 表的完整日期范围，再做早期回填 + 过渡 + 下限
    all_dates = sorted(set(spot_usd_df["date"].dropna()).union(set(futures_usd_df["date"].dropna())))
    factor_df_extended = reindex_factor_to_dates(factor_df, all_dates)
    factor_df_filled = backfill_early_factors(
        factor_df_extended,
        anchor_window=EARLY_FACTOR_ANCHOR_WINDOW,
        transition_window=EARLY_FACTOR_TRANSITION_WINDOW,
        floor=EARLY_FACTOR_FLOOR,
    )

    spot_usd_adj_df = apply_factors_to_wide(spot_usd_df, factor_df_filled, SPOT_KEEP_EXCHANGES)
    futures_usd_adj_df = apply_factors_to_wide(futures_usd_df, factor_df_filled, FUTURES_KEEP_EXCHANGES)

    spot_monthly_df = monthly_summary(spot_usd_adj_df, "date")
    futures_monthly_df = monthly_summary(futures_usd_adj_df, "date")

    closed = list_closed_months()
    sheets = {
        "现货": spot_df,
        "合约": futures_df,
        "现货usd": spot_usd_df,
        "合约usd": futures_usd_df,
        "现货usd调整": spot_usd_adj_df,
        "合约usd调整": futures_usd_adj_df,
        "现货月度": spot_monthly_df,
        "合约月度": futures_monthly_df,
    }
    # 写前备份，再与旧文件合并冻结 closed 月（失败则中止，避免空盖）
    backup_pipeline_xlsx(OUTPUT_XLSX)
    old_sheets = load_old_pipeline_sheets(OUTPUT_XLSX)
    sheets = apply_closed_month_freeze(sheets, old_sheets, closed)

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)

    print(f"输出完成: {OUTPUT_XLSX}")
    print(f"因子已更新: {FACTOR_DAILY_CSV}")
    print(f"现货日度行数: {len(sheets['现货'])}, 合约日度行数: {len(sheets['合约'])}")
    print(f"本次使用 k: {k_value}")


if __name__ == "__main__":
    main()

