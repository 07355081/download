"""Download UNI Burn metrics from Dune Analytics into local JSON files.

窄查询只扫最近两个完整 UTC 日(见 query.sql)。全历史 query 8260046 一次大约 49 credits,
已放进 _dune.AUTO_EXECUTE_BLOCKLIST,日常不会再跑。本地 JSON 里更早的日期按 day 保留。

Outputs:
  output/json/daily_by_chain.json   结构见 _dune.build_payload

Usage:
  python download.py                      # 缓存超过 20h 才触发执行
  python download.py --dry-run            # 只看缓存新鲜度
  python download.py --no-execute         # 只读缓存(不花执行额度)
  python download.py --force-execute      # 无视缓存年龄,强制跑一次
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT_DIR = HERE.parent                       # /root/data-download
JSON_DIR = HERE / "output" / "json"

sys.path.insert(0, str(ROOT_DIR))
from _dune import add_common_args, run_module  # noqa: E402

# 2026-10-02 从 8260046 收窄。实测约 1.1 credits / 次,可以每天跑。
NARROW_QUERY_ID = 8884655

QUERIES: dict[str, tuple[int, str]] = {
    "daily_by_chain": (
        NARROW_QUERY_ID,
        "UNI 按链日度销毁量及美元、各链累积销毁",
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", default=None, choices=sorted(QUERIES.keys()), help="只拉某一个 query(按 name)")
    parser.add_argument("--out", default=str(JSON_DIR), help="输出目录(默认 output/json)")
    add_common_args(parser, default_max_age_hours=20.0)
    args = parser.parse_args()

    run_module(
        "uni-burn",
        QUERIES,
        args,
        Path(args.out),
        ROOT_DIR,
        merge_on={"daily_by_chain": "day"},
    )


if __name__ == "__main__":
    main()
