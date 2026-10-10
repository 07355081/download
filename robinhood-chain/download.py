"""Download Robinhood Chain metrics from Dune Analytics into local JSON files.

数据来源:日交易笔数和 DEX 成交额是本账号的窄查询(见 daily_transactions.sql、
dex_volume.sql),直接读 robinhood.transactions 与 dex.trades。其余仍用 adam_tehc
的公开 query。

刷新口径:
  自有的两条窄查询只返回最近两个完整 UTC 日,缓存超过 20 小时就自己执行,再按 day
  把新日期合并进本地历史。2026-09-23 之后、上传表停更造成的空档在首次执行时补过。
  别人的 query 默认 max_age_hours=48:平时纯读缓存;只有对方停了调度、缓存放到两天以上,
  才由我们自己触发一次执行。

关于 launchpad_activity:
  全历史 query 8024180 一次大约 315 credits,已禁止自动执行。
  窄查询 8884658 只返回最近两个完整 UTC 日,但实测执行大约 517 credits
  (依赖的子查询仍会重算)。因此单独把刷新间隔放到 72 小时,而不是退回全历史查询。
  更早的日期按 day 合并留在本地文件里。volume_usd 的 bullmarkets 异常值仍由前端剔除。

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
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT_DIR = HERE.parent                       # /root/data-download
JSON_DIR = HERE / "output" / "json"

sys.path.insert(0, str(ROOT_DIR))
from _dune import API_BASE, add_common_args, resolve_api_key, run_module  # noqa: E402

CATCHUP_PREDICATE = "DATE '2026-09-23'"
OWNED_SQL = {
    8894974: HERE / "daily_transactions.sql",
    8894975: HERE / "dex_volume.sql",
}

# name -> (query_id, 中文说明)。name 即输出文件名 <name>.json,前端也按此约定读取。
# 改动这里必须同步 copy_to_dashboard.py 的 ROBINHOOD_JSON_FILES 与前端路由白名单。
# 日交易笔数、DEX 成交额不再使用 adam_tehc 的 7915115 / 7915153(那两张只读上传表)。
QUERIES: dict[str, tuple[int, str]] = {
    "daily_transactions": (
        8894974,
        "Robinhood Chain 日交易笔数",
    ),
    "active_wallets": (
        7916628,
        "Robinhood Chain 日活跃地址(new 新增 / returning 回访)",
    ),
    "dex_volume": (
        8894975,
        "Robinhood Chain DEX 日成交量(全链合计)",
    ),
    "launchpad_new_tokens": (
        7916783,
        "Meme Launchpad 每日新发代币数(按 launchpad)",
    ),
    "launchpad_activity": (
        8884658,
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


def _last_days(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    return sorted({str(row.get("day"))[:10] for row in rows if isinstance(row, dict) and row.get("day")})


def narrow_owned_queries_after_catchup() -> None:
    """补洞数据落地后，把 Dune 上的 SQL 收成最近两个完整日。失败只打印，不挡每日任务。"""
    days = _last_days(JSON_DIR / "daily_transactions.json")
    if not days or days[-1] < "2026-10-01" or not any(day >= "2026-09-23" for day in days):
        return
    try:
        api_key = resolve_api_key(None, ROOT_DIR)
    except SystemExit as exc:
        print(f"[robinhood-chain] 跳过收窄查询: {exc}")
        return
    headers = {"X-Dune-API-Key": api_key, "Content-Type": "application/json"}
    for query_id, sql_path in OWNED_SQL.items():
        try:
            request = urllib.request.Request(f"{API_BASE}/query/{query_id}", headers=headers)
            with urllib.request.urlopen(request, timeout=60) as response:
                current = json.load(response).get("query_sql") or ""
            if CATCHUP_PREDICATE not in current:
                continue
            body = json.dumps({"query_sql": sql_path.read_text(encoding="utf-8")}).encode("utf-8")
            update = urllib.request.Request(
                f"{API_BASE}/query/{query_id}",
                data=body,
                headers=headers,
                method="PATCH",
            )
            with urllib.request.urlopen(update, timeout=60) as response:
                response.read()
            print(f"[robinhood-chain] query {query_id} 已收窄为最近两个完整 UTC 日")
        except Exception as exc:  # noqa: BLE001
            print(f"[robinhood-chain] query {query_id} 收窄失败: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", default=None, choices=sorted(QUERIES.keys()), help="只拉某一个 query(按 name)")
    parser.add_argument("--out", default=str(JSON_DIR), help="输出目录(默认 output/json)")
    add_common_args(parser, default_max_age_hours=48.0)
    args = parser.parse_args()

    try:
        run_module(
            "robinhood-chain",
            QUERIES,
            args,
            Path(args.out),
            ROOT_DIR,
            merge_on={
                "launchpad_activity": "day",
                "daily_transactions": "day",
                "dex_volume": "day",
            },
            max_age_overrides={
                "launchpad_activity": 72.0,
                "daily_transactions": 20.0,
                "dex_volume": 20.0,
            },
        )
    finally:
        narrow_owned_queries_after_catchup()


if __name__ == "__main__":
    main()
