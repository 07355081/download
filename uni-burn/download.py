"""Download UNI Burn metrics from Dune Analytics into local JSON files.

Data source: Dune query 8260046 (daily UNI burns by chain + USD + cumulative).

刷新口径(2026-08 修正):
  query 8260046 属于 Dune 用户 oasishub,不是本账号的查询,Dune 上没有任何调度会自动
  重跑它 —— 只读 get_latest_result 会永远拿到"上一次有人手动跑"的缓存快照。线上面板
  就这么卡在 2026-08-16,连发六天同一份 677 行数据。
  现在改为:缓存超过 --max-age-hours 就自己触发一次执行(见 _dune.py)。一次约 40
  credits / 30 秒,每天至多一次。

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

# name -> (query_id, 中文说明)。name 即输出文件名 <name>.json,前端也按此约定读取。
QUERIES: dict[str, tuple[int, str]] = {
    "daily_by_chain": (
        8260046,
        "UNI 按链日度销毁量及美元、各链累积销毁",
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", default=None, choices=sorted(QUERIES.keys()), help="只拉某一个 query(按 name)")
    parser.add_argument("--out", default=str(JSON_DIR), help="输出目录(默认 output/json)")
    add_common_args(parser, default_max_age_hours=20.0)
    args = parser.parse_args()

    run_module("uni-burn", QUERIES, args, Path(args.out), ROOT_DIR)


if __name__ == "__main__":
    main()
