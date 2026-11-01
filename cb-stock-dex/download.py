#!/usr/bin/env python3
"""Coinbase 在 Base 上发行的股票代币，DEX 日成交。

Dune query 8958296，表 dex.trades，链 Base。
一笔兑换只计一行。名单是已知的 0xb200… 合约，不是 Token Terminal 的原表。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "output" / "json"

sys.path.insert(0, str(ROOT))
from _dune import add_common_args, run_module  # noqa: E402

QUERIES = {
    "cb_stock_dex": (
        8958296,
        "Base 上 Coinbase 股票代币的 DEX 日成交，按项目和版本拆开",
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default=None, choices=sorted(QUERIES))
    parser.add_argument("--out", default=str(OUT))
    add_common_args(parser, default_max_age_hours=30.0)
    args = parser.parse_args()
    run_module("cb-stock-dex", QUERIES, args, Path(args.out), ROOT)


if __name__ == "__main__":
    main()
