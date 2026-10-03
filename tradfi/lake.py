"""TradFi Parquet 数据湖读取层 —— 量化代码统一从这里读 tradfi-price / tradfi-oi / tradfi-funding。

数据来源：VPS 每日管道写 parquet → 既有 rsync 拉取规则（递归 *.parquet）同步到本机
{LAKE_ROOT}/tradfi-*/。本模块只读、不写湖，用 DuckDB 直接对 parquet 文件建视图，零 ETL、零复制。

目录布局：
    tradfi-price/<EXCHANGE>/<EXCHANGE>_<SYMBOL>_<interval>.parquet
        time(ms), open, high, low, close, volume_usd,
        exchange, symbol, base_asset, sector, interval, form, ingested_at(ms, PIT)
    tradfi-oi/<EXCHANGE>/<EXCHANGE>_<SYMBOL>_<interval>.parquet
        time(ms), oi_usd, exchange, symbol, base_asset, sector, interval, ingested_at(ms, PIT)
    tradfi-funding/<EXCHANGE>/<EXCHANGE>_<SYMBOL>.parquet   （无 interval 语义；文件名也不带 interval）
        time(ms), rate, exchange, symbol, base_asset, sector, ingested_at(ms, PIT)
        （schema 里仍有 interval/form 两列以便 union_by_name，但 funding 场景下恒为空，忽略即可）

建模注意事项（供下游用户参考；2026-07-14 VPS 修复 MEXC/Crypto.com OI 端点失效 +
Kraken 个股发现漏检/funding 时间解析 bug 后更新）：
- OI 历史深度不均：Gate / Bybit / Binance 有真实多点历史；
  其余（OKX / Bitget / MEXC / HTX / Coinbase / Kraken）是每日快照，从各自上线时刻起
  逐日累积 1 点/天，早期回看窗口会短，不要拿这几家去算"历史 OI 变化率"之类需要长序列的指标。
  Crypto.com 名义上 oi_history=True（历史接口），修复后已有数据，但深度需自行核实。
- funding：除 Coinbase 外全部有历史（含 Kraken，修复前曾长期为 0 行，现已回补）。
  Coinbase INTX 没有 funding 文件，是正常现象（该所无 funding 产品）。
- Kraken 标的数已从修复前的 1 个涨到 14 个 perp（新增 xStocks 个股 AAPL/TSLA/NVDA/MSTR/
  HOOD/GOOGL/CRCL/SPCX/ANTHROPIC/OPENAI + QQQ/SPY/GLD/WTIOIL）+ price 里另有 SPX/PAXG/XAUT
  等非 perp 标的；按固定标的数做校验/告警的下游代码需要放宽 Kraken 的预期值。
- 单位：oi_usd 是美元名义持仓；rate 是当期资金费率小数（0.0001 = 0.01%）。
- PIT：ingested_at 是数据落库时刻，不是数据所属时刻（time）。要做严格无未来函数的回测，
  应按 ingested_at 过滤（只用回测"当时"已经落库的数据），而不是只按 time 对齐——
  本模块的 daily_panel 默认不做这层过滤，需要严格 PIT 的下游自己在查询里加条件。

依赖：`pip install duckdb`（本机原生 Windows Python 未预装；WSL 内 python3 已有 1.5.4）。
load_daily_panel() 额外需要 pandas/numpy 才能转成 DataFrame；只用 connect() 写 SQL 则不需要。
"""
from __future__ import annotations

import os
import platform
from typing import Literal

import duckdb

Dataset = Literal["price", "oi", "funding"]

DATASETS: dict[Dataset, str] = {
    "price": "tradfi-price",
    "oi": "tradfi-oi",
    "funding": "tradfi-funding",
}


def _default_lake_root() -> str:
    env = os.environ.get("TRADFI_LAKE_ROOT")
    if env:
        return env
    # WSL/Linux 下本机 E 盘挂载在 /mnt/e；原生 Windows Python 下直接用 E:/
    return "/mnt/e/data-lake" if platform.system() != "Windows" else "E:/data-lake"


LAKE_ROOT = _default_lake_root()


def glob_for(dataset: Dataset, root: str | None = None) -> str:
    base = (root or LAKE_ROOT).rstrip("/")
    return f"{base}/{DATASETS[dataset]}/**/*.parquet"


def connect(root: str | None = None) -> duckdb.DuckDBPyConnection:
    """打开一个已注册好三张原始视图（tradfi_price / tradfi_oi / tradfi_funding）的 DuckDB 连接。

    视图是惰性扫描 parquet，不会一次性加载进内存；`union_by_name=true` 让三者各自内部
    不同交易所/不同批次写入若存在列顺序差异也能对齐。
    """
    con = duckdb.connect()
    for name in DATASETS:
        pattern = glob_for(name, root)  # type: ignore[arg-type]
        con.execute(
            f"CREATE OR REPLACE VIEW tradfi_{name} AS "
            f"SELECT * FROM read_parquet('{pattern}', union_by_name=true)"
        )
    return con


def daily_panel_sql(interval: str = "1d") -> str:
    """把 price / oi / funding 按 exchange+symbol+date 对齐成一张日频宽表的 SQL。

    - price / oi 按 `interval` 过滤到指定周期（默认日线）。
    - funding 没有 interval，按天聚合：funding_rate_last 取当天最后一条 rate，
      funding_rate_sum 取当天 rate 之和（多数所每 8h 收一次，sum 近似"当天资金成本"）。
    - 以 price 为主表 LEFT JOIN oi / funding：某所某标的当天若无 OI/funding，对应列为 NULL，
      不会因为 OI 是"仅快照"或 funding 缺失（如 Coinbase INTX）而丢价格行。
    """
    return f"""
    WITH price AS (
        SELECT exchange, symbol, base_asset, sector,
               CAST(to_timestamp(time / 1000.0) AS DATE) AS date,
               time, open, high, low, close, volume_usd,
               ingested_at AS price_ingested_at
        FROM tradfi_price
        WHERE interval = '{interval}'
    ),
    oi AS (
        SELECT exchange, symbol,
               CAST(to_timestamp(time / 1000.0) AS DATE) AS date,
               arg_max(oi_usd, time) AS oi_usd,
               max(ingested_at) AS oi_ingested_at
        FROM tradfi_oi
        WHERE interval = '{interval}'
        GROUP BY 1, 2, 3
    ),
    funding AS (
        SELECT exchange, symbol,
               CAST(to_timestamp(time / 1000.0) AS DATE) AS date,
               arg_max(rate, time) AS funding_rate_last,
               sum(rate) AS funding_rate_sum,
               count(*) AS funding_n,
               max(ingested_at) AS funding_ingested_at
        FROM tradfi_funding
        GROUP BY 1, 2, 3
    )
    SELECT
        price.exchange, price.symbol, price.base_asset, price.sector, price.date,
        price.open, price.high, price.low, price.close, price.volume_usd,
        oi.oi_usd,
        funding.funding_rate_last, funding.funding_rate_sum, funding.funding_n,
        price.price_ingested_at, oi.oi_ingested_at, funding.funding_ingested_at
    FROM price
    LEFT JOIN oi ON oi.exchange = price.exchange AND oi.symbol = price.symbol AND oi.date = price.date
    LEFT JOIN funding ON funding.exchange = price.exchange AND funding.symbol = price.symbol AND funding.date = price.date
    ORDER BY price.exchange, price.symbol, price.date
    """


def load_daily_panel(interval: str = "1d", root: str | None = None):
    """把日频宽表拉成 pandas DataFrame（需要 pandas/numpy）。数据量大时建议改用 connect() 自己分片查询。"""
    con = connect(root)
    return con.execute(daily_panel_sql(interval)).df()


def coverage_report(root: str | None = None) -> dict[str, list[tuple]]:
    """各所 syms/rows/时间范围速览 —— 等价于同步后做的抽查 SQL，用来快速核对数据是否正常。"""
    con = connect(root)
    price = con.execute(
        "SELECT exchange, count(DISTINCT symbol) syms, count(*) n_rows, min(time) t0, max(time) t1 "
        "FROM tradfi_price GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    oi = con.execute(
        "SELECT exchange, count(DISTINCT symbol) syms, count(*) n_rows, min(time) t0, max(time) t1 "
        "FROM tradfi_oi GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    funding = con.execute(
        "SELECT exchange, count(DISTINCT symbol) syms, count(*) n_rows "
        "FROM tradfi_funding GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    return {"price": price, "oi": oi, "funding": funding}


if __name__ == "__main__":
    for name, rows in coverage_report().items():
        print(f"=== {name} ===")
        for r in rows:
            print(r)
        print()
