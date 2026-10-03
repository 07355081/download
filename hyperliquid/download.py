"""Download Hyperliquid metrics into local JSON files.

HIP-3 / HIP-4 的小表仍读 ASXN 的 Dune 缓存，超过 36h 才自己执行。
按币种的大表 hip3_by_symbol 改走 Hyperliquid 官方接口，不再下载 Dune 8077453。

Outputs (one file per query):
  output/json/<name>.json

Usage:
  python download.py                      # Dune 小表 + 官方接口按币种
  python download.py --only hip3_overview
  python download.py --only hip3_by_symbol
  python download.py --dry-run            # 只看 Dune 缓存新鲜度
  python download.py --no-execute         # Dune 只读缓存；按币种仍走官方接口
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT_DIR = HERE.parent
JSON_DIR = HERE / "output" / "json"
SYMBOL_NAME = "hip3_by_symbol"

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT_DIR))
from _dune import add_common_args, run_module  # noqa: E402
from download_symbols import update_hip3_by_symbol  # noqa: E402

# name -> (query_id, 中文说明)。name 即输出文件名 <name>.json，前端也按此约定读取。
# hip3_by_symbol 不在这里：整包结果大约 4 万行，每天导出要几十 credits。
QUERIES: dict[str, tuple[int, str]] = {
    "hip3_overview":     (8077142, "Hyperliquid HIP-3 整体交易量和持仓量"),
    "hip3_by_category":  (8077405, "Hyperliquid HIP-3 按资产类别分类交易量和持仓量"),
    "hip3_by_market":    (8077374, "Hyperliquid HIP-3 按部署市场分类的交易量和持仓量"),
    "hl_by_category":    (8077484, "Hyperliquid 按资产类别分类交易量"),
    "hip3_and_crypto":   (8077513, "Hyperliquid HIP-3 和 Crypto 分类交易量、持仓量及市场份额"),
    "hip4":              (8077860, "Hyperliquid HIP-4 交易量、交易笔数和活跃市场"),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--only",
        default=None,
        choices=sorted([*QUERIES.keys(), SYMBOL_NAME]),
        help="只拉某一个输出(按 name)",
    )
    parser.add_argument("--out", default=str(JSON_DIR), help="输出目录(默认 output/json)")
    # ASXN 日更大约落后 1 天。36h 内复用缓存；超过才自己执行，避免 owner 停调度后静默冻住。
    add_common_args(parser, default_max_age_hours=36.0)
    args = parser.parse_args()

    out_dir = Path(args.out)
    dune_failed = False

    if args.only != SYMBOL_NAME:
        try:
            run_module("hyperliquid", QUERIES, args, out_dir, ROOT_DIR)
        except SystemExit as exc:
            dune_failed = bool(exc.code)
            if args.only:
                raise

    if args.only in (None, SYMBOL_NAME):
        if args.dry_run:
            print("hip3_by_symbol 使用 Hyperliquid 官方接口,不读 Dune;dry-run 不写文件")
        else:
            print("[hyperliquid] hip3_by_symbol ← Hyperliquid 官方接口")
            update_hip3_by_symbol(out_dir)

    if dune_failed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
