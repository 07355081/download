"""将 base_asset_categories.csv 汇总为 JSON 标签。

输入：
  output/csv/base_asset_categories.csv

输出：
  output/json/base_asset_categories_summary.json

汇总规则：
  1. 始终纳入 CoinGecko_categories 列中的标签（| 分隔，可多个）。
  2. OKX / Gate 列若出现 stocks 或 Stocks（不区分大小写），额外增加标签 Stocks。
  3. 标签可叠加；MEXC 列不参与汇总。
  4. 输出 tags 按字母序排序、去重。

示例（AAPLON）：
  CoinGecko: Binance Alpha Spotlight|Real World Assets (RWA)
  Gate: stocks
  -> ["Binance Alpha Spotlight", "Real World Assets (RWA)", "Stocks"]

运行：
  python summarize_base_asset_categories.py          # 单独从 CSV 汇总
  classify_coinglass_instruments.py 写 CSV 后默认也会调用本模块
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IN_CSV = ROOT / "output" / "csv" / "base_asset_categories.csv"
OUT_JSON = ROOT / "output" / "json" / "base_asset_categories_summary.json"

COL_BASE = "base_asset"
COL_GECKO = "CoinGecko_categories"
COL_OKX = "OKX"
COL_GATE = "Gate"
STOCKS_LABEL = "Stocks"


def split_tags(raw: str) -> list[str]:
    """把 | 分隔的标签字符串拆成非空列表。"""
    return [p.strip() for p in (raw or "").split("|") if p.strip()]


def is_stocks_marker(tag: str) -> bool:
    """OKX / Gate 是否标记为 stocks（大小写不敏感）。"""
    return tag.strip().lower() == "stocks"


def summarize_tags(gecko: str, okx: str, gate: str) -> list[str]:
    """按规则汇总单个 base_asset 的标签。"""
    tags: set[str] = set(split_tags(gecko))

    for exchange_tag in split_tags(okx) + split_tags(gate):
        if is_stocks_marker(exchange_tag):
            tags.add(STOCKS_LABEL)

    return sorted(tags)


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise SystemExit(f"未找到输入 CSV: {path}\n  请先运行 classify_coinglass_instruments.py")
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def build_summary(
    rows: list[dict[str, str]],
    *,
    source_csv: Path | str = IN_CSV,
) -> dict:
    assets: dict[str, list[str]] = {}
    with_tags = 0
    with_stocks = 0

    for row in rows:
        base = str(row.get(COL_BASE) or "").strip()
        if not base:
            continue
        tags = summarize_tags(
            str(row.get(COL_GECKO) or ""),
            str(row.get(COL_OKX) or ""),
            str(row.get(COL_GATE) or ""),
        )
        assets[base] = tags
        if tags:
            with_tags += 1
        if STOCKS_LABEL in tags:
            with_stocks += 1

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_csv": str(source_csv),
        "rule": (
            "CoinGecko 标签全部保留；OKX/Gate 为 stocks/Stocks 时追加 Stocks；MEXC 不参与"
        ),
        "asset_count": len(assets),
        "with_tags_count": with_tags,
        "with_stocks_count": with_stocks,
        "assets": assets,
    }


def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def write_summary_from_rows(
    rows: list[dict[str, str]],
    *,
    out_json: Path = OUT_JSON,
    source_csv: Path | str = IN_CSV,
) -> dict:
    doc = build_summary(rows, source_csv=source_csv)
    write_json(out_json, doc)

    print(f"汇总 {doc['asset_count']} 个 base_asset", flush=True)
    print(f"  有标签: {doc['with_tags_count']}", flush=True)
    print(f"  含 Stocks: {doc['with_stocks_count']}", flush=True)

    sample = doc["assets"].get("AAPLON")
    if sample is not None:
        print(f"  AAPLON 示例: {sample}", flush=True)

    print(f"已写入 -> {out_json}", flush=True)
    return doc


def main() -> int:
    write_summary_from_rows(load_csv(IN_CSV))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
