"""Load every scalar indicator JSON into a wide daily feature matrix."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from data_loader import JSON_COINGLASS, JSON_GLASSNODE, _legacy_series, _pipeline_series, _to_date_series, load_btc_close


def _parse_value(v: Any) -> dict[str, float]:
    """Turn one observation into flat numeric fields."""
    out: dict[str, float] = {}
    if v is None:
        return out
    if isinstance(v, bool):
        out["v"] = float(v)
        return out
    if isinstance(v, (int, float)):
        fv = float(v)
        if np.isfinite(fv):
            out["v"] = fv
        return out
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return out
        try:
            return _parse_value(json.loads(s))
        except json.JSONDecodeError:
            n = pd.to_numeric(s, errors="coerce")
            if np.isfinite(n):
                out["v"] = float(n)
            return out
    if isinstance(v, dict):
        for k, sub in v.items():
            for sk, sv in _parse_value(sub).items():
                key = f"{k}_{sk}" if sk != "v" else str(k)
                out[key] = sv
        return out
    return out


def _series_from_legacy(path: Path, prefix: str) -> pd.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("series") or []
    if not rows:
        return pd.DataFrame()
    dates = _to_date_series(pd.Series([r["t"] for r in rows]))
    records: list[dict[str, float]] = []
    cols: set[str] = set()
    for r in rows:
        flat = _parse_value(r.get("v"))
        records.append(flat)
        cols.update(flat.keys())
    if not cols:
        return pd.DataFrame()
    data = {f"{prefix}_{c}": [rec.get(c, np.nan) for rec in records] for c in cols}
    df = pd.DataFrame(data, index=dates)
    return df.groupby(level=0).last().sort_index()


def _series_from_pipeline(path: Path, prefix: str) -> pd.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8"))
    data = payload.get("data")
    if isinstance(data, list):
        # snapshot / list-of-dicts — skip (no daily series)
        return pd.DataFrame()
    if not isinstance(data, dict) or "time_list" not in data:
        return pd.DataFrame()
    dates = _to_date_series(pd.Series(data["time_list"]))
    raw = data["data_list"]
    records = [_parse_value(item) for item in raw]
    cols: set[str] = set()
    for rec in records:
        cols.update(rec.keys())
    if not cols:
        return pd.DataFrame()
    frame = {
        f"{prefix}_{c}": [rec.get(c, np.nan) for rec in records]
        for c in cols
    }
    df = pd.DataFrame(frame, index=dates)
    return df.groupby(level=0).last().sort_index()


def _load_one(path: Path, prefix: str) -> pd.DataFrame:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return pd.DataFrame()
    if "series" in payload:
        return _series_from_legacy(path, prefix)
    if isinstance(payload.get("data"), dict) and "time_list" in payload["data"]:
        return _series_from_pipeline(path, prefix)
    return pd.DataFrame()


def _collect_paths(cg_root: Path, gn_root: Path) -> list[tuple[str, Path]]:
    """Return (prefix, path) from coinglass + glassnode JSON subdirs."""
    items: list[tuple[str, Path]] = []

    if cg_root.is_dir():
        for p in sorted(cg_root.glob("*.json")):
            stem = p.stem.replace("-", "_")
            items.append((stem, p))

    if gn_root.is_dir():
        for p in sorted(gn_root.glob("*.json")):
            prefix = p.stem.replace("-", "_").lower()
            items.append((prefix, p))

    return items


def load_all_features(
    json_coinglass: Path | None = None,
    json_glassnode: Path | None = None,
    *,
    min_coverage: float = 0.35,
    verbose: bool = True,
) -> pd.DataFrame:
    cg = json_coinglass or JSON_COINGLASS
    gn = json_glassnode or JSON_GLASSNODE
    close = load_btc_close()
    panel = pd.DataFrame({"close": close})
    panel["ret"] = panel["close"].pct_change()

    paths = _collect_paths(cg, gn)
    loaded = 0
    skipped = 0
    blocks: list[pd.DataFrame] = []
    for prefix, path in paths:
        if prefix in ("close", "ret"):
            continue
        block = _load_one(path, prefix)
        if block.empty:
            skipped += 1
            continue
        blocks.append(block)
        loaded += 1
        if verbose and loaded % 25 == 0:
            print(f"  loaded {loaded} indicators...", flush=True)
    if blocks:
        panel = pd.concat([panel, *blocks], axis=1)

    if verbose:
        print(f"Feature files loaded: {loaded}, skipped empty: {skipped}")

    # Drop duplicate columns (identical glassnode vs legacy)
    panel = panel.loc[:, ~panel.columns.duplicated()]

    feat_cols = [c for c in panel.columns if c not in ("close", "ret")]
    coverage = panel[feat_cols].notna().mean()
    keep = coverage[coverage >= min_coverage].index.tolist()
    panel = panel[["close", "ret", *keep]]

    if verbose:
        print(f"Features after coverage>={min_coverage:.0%}: {len(keep)} columns")
        print(f"Date span: {panel.index.min().date()} .. {panel.index.max().date()}")

    return panel.sort_index()
