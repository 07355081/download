"""Load BTC daily close + selected on-chain/sentiment indicators into one DataFrame."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
BTC_INDEX_ROOT = SCRIPT_DIR.parent
JSON_ROOT = BTC_INDEX_ROOT / "output" / "json"
JSON_COINGLASS = JSON_ROOT / "coinglass"
JSON_GLASSNODE = JSON_ROOT / "glassnode"
DEFAULT_PRICE = (
    BTC_INDEX_ROOT.parent
    / "coinglass-history"
    / "spot-price-history"
    / "output"
    / "json"
    / "Binance_BTCUSDT_1d.json"
)


def _to_date_series(ts: pd.Series) -> pd.Series:
    """Normalize ms or s timestamps to UTC date."""
    s = pd.to_numeric(ts, errors="coerce")
    if s.dropna().empty:
        return pd.to_datetime(ts, utc=True, errors="coerce").dt.normalize()
    med = float(s.dropna().median())
    unit = "ms" if med > 1e11 else "s"
    return pd.to_datetime(s, unit=unit, utc=True, errors="coerce").dt.normalize()


def load_btc_close(price_path: Path | None = None) -> pd.Series:
    path = price_path or DEFAULT_PRICE
    if not path.exists():
        raise FileNotFoundError(f"BTC price file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("data") or []
    df = pd.DataFrame(rows)
    if df.empty or "close" not in df.columns:
        raise ValueError(f"No OHLC rows in {path}")
    df["date"] = _to_date_series(df["time"])
    df = df.dropna(subset=["date"]).sort_values("date")
    return df.set_index("date")["close"].astype(float)


def _legacy_series(path: Path) -> pd.Series:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "series" not in payload:
        raise ValueError(f"Expected legacy series format: {path}")
    rows = payload["series"]
    dates = _to_date_series(pd.Series([r["t"] for r in rows]))
    vals = pd.to_numeric([r["v"] for r in rows], errors="coerce")
    s = pd.Series(vals, index=dates, name=path.stem)
    return s.groupby(level=0).last().sort_index()


def _pipeline_series(path: Path, value_key: str | None = None) -> pd.Series:
    payload = json.loads(path.read_text(encoding="utf-8"))
    data = payload.get("data")
    if not isinstance(data, dict) or "time_list" not in data:
        raise ValueError(f"Expected pipeline time_list format: {path}")
    dates = _to_date_series(pd.Series(data["time_list"]))
    raw = data["data_list"]
    if value_key:
        vals = [item.get(value_key) if isinstance(item, dict) else item for item in raw]
    else:
        vals = []
        for item in raw:
            if isinstance(item, dict):
                if len(item) == 1:
                    vals.append(next(iter(item.values())))
                else:
                    vals.append(item.get("value", float("nan")))
            else:
                vals.append(item)
    s = pd.Series(pd.to_numeric(vals, errors="coerce"), index=dates, name=path.stem)
    return s.groupby(level=0).last().sort_index()


def load_feature_panel(
    json_root: Path | None = None,
    price_path: Path | None = None,
) -> pd.DataFrame:
    root = json_root or JSON_ROOT

    close = load_btc_close(price_path)
    panel = pd.DataFrame({"close": close})

    mvrv_path = JSON_GLASSNODE / "001_mvrv-z-score.json"
    if mvrv_path.exists():
        panel["mvrv_z"] = _legacy_series(mvrv_path)
    else:
        gn = JSON_GLASSNODE / "001_mvrv-z-score.json"
        panel["mvrv_z"] = _pipeline_series(gn)

    fg_path = JSON_COINGLASS / "fear-greed-history.json"
    panel["fear_greed"] = _pipeline_series(fg_path)

    ma_path = JSON_COINGLASS / "200-week-moving-average-heatmap.json"
    panel["ma_200w"] = _pipeline_series(ma_path, value_key="moving_average_1440")

    panel = panel.sort_index()
    panel["ret"] = panel["close"].pct_change()
    panel["above_200w"] = (panel["close"] > panel["ma_200w"]).astype(float)
    return panel
