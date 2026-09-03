"""Download Robinhood Chain metrics from Dune Analytics into local JSON files.

数据来源:Dune 用户 adam_tehc 的 7 条公开 query(见 QUERIES)。

刷新口径:
  这些 query 都不属于本账号,但 owner 给大部分挂了调度,缓存通常只有几小时。所以默认
  max_age_hours=48:平时纯读缓存、零执行额度;只有 owner 停了调度、缓存放到两天以上,
  才由我们自己触发一次执行接管。这样既不白花 credits,也不会像 UNI Burn 那样在 owner
  不跑之后无声无息地一直发旧数据。

关于 launchpad_activity(query 8024180):
  它同时含 volume_usd / trades / wallets 三列,一份数据就覆盖了"代币日交易量"和"代币
  活跃交易者"两个指标,不需要再拉 7979343(那是同一份数据的旧版本,2579 个键与本表完全
  重叠)。但本表的 volume_usd 有个已知脏数据:launchpad `bullmarkets` 有 24 天的成交量
  被错误定价放大到万亿美元级(单日峰值 8.7e13,而整条链一天的 DEX 成交量才 3.5e8)。
  这里保持源数据原样落盘不做清洗,由前端聚合层按阈值剔除并在面板上标注,便于溯源。

Outputs:
  output/json/<name>.json           结构见 _dune.build_payload

Usage:
  python download.py                      # 缓存超过 48h 才触发执行
  python download.py --dry-run            # 只看 7 条 query 的缓存新鲜度
  python download.py --only rwa_aum       # 只拉一条
  python download.py --no-execute         # 只读缓存(不花执行额度)
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
# 改动这里必须同步 copy_to_dashboard.py 的 ROBINHOOD_JSON_FILES 与前端路由白名单。
QUERIES: dict[str, tuple[int, str]] = {
    "daily_transactions": (
        7915115,
        "Robinhood Chain 日交易笔数",
    ),
    "active_wallets": (
        7916628,
        "Robinhood Chain 日活跃地址(new 新增 / returning 回访)",
    ),
    "dex_volume": (
        7915153,
        "Robinhood Chain DEX 日成交量(按 DEX)",
    ),
    "launchpad_new_tokens": (
        7916783,
        "Meme Launchpad 每日新发代币数(按 launchpad)",
    ),
    "launchpad_activity": (
        8024180,
        "Meme Launchpad 代币日成交量 / 交易笔数 / 活跃交易者(按 launchpad)",
    ),
    "rwa_aum": (
        8073404,
        "Robinhood Chain RWA 市值(按股票 / ETF / 大宗商品 / 国债分类)",
    ),
    # 注意:8017940 是别人的 BTC 5m 出块 query,不是这条。币股/RWA 成交量是 8071940。
    "rwa_volume": (
        8071940,
        "Robinhood Chain RWA 日成交量(股票现货 / Meme×股票 / 商品 / ETF与国债)",
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", default=None, choices=sorted(QUERIES.keys()), help="只拉某一个 query(按 name)")
    parser.add_argument("--out", default=str(JSON_DIR), help="输出目录(默认 output/json)")
    add_common_args(parser, default_max_age_hours=48.0)
    args = parser.parse_args()

    run_module("robinhood-chain", QUERIES, args, Path(args.out), ROOT_DIR)


if __name__ == "__main__":
    main()
