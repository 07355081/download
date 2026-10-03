"""数据湖每日健康检查 —— 覆盖率 + 时效性 + 拉取管道存活。只写日志/退出码，不产出网页数据。

检查三件事：
1. 拉取管道存活：今日 pull_outbox.sh 日志是否存在、是否有近期活动、有没有连续失败。
2. 覆盖率：price/oi/funding 三个数据集里，各交易所是否都出现了（缺的记 WARN，
   已知会缺的——比如 funding 里的 Coinbase——不在 EXPECTED 集合内，不会误报）。
3. 时效性：已出现过的交易所，最新一条 ingested_at 是否在阈值内（超过判定为"停更"，记 ERROR）。

用法：
    python3 check_lake_coverage.py            # 打印摘要 + 追加写日志
    python3 check_lake_coverage.py --json      # 额外打印一份 JSON 摘要，方便脚本解析

退出码：
    0 = 没有 ERROR（可能有 WARN）
    1 = 有 ERROR（数据集整体空 / 已有交易所停更 / 拉取管道长时间无活动或连续失败）

注册为 Windows 计划任务：见同目录 register_coverage_task.ps1（每天跑一次，建议排在
VPS 09:00 CST 每日任务 + 本机 30 分钟拉取窗口之后，比如本机 10:30）。
"""
from __future__ import annotations

import argparse
import json as jsonlib
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tradfi"))
import lake  # noqa: E402

# ── 已知业务事实（对应 tradfi/方案-可行性与网络.md、lake.py 文档，不要凭感觉改）──
ALL_EXCHANGES = {
    "OKX", "Gate", "Bitget", "MEXC", "HTX", "Crypto.com",
    "Coinbase", "Kraken", "Bybit", "Binance",
}
EXPECTED: dict[str, set[str]] = {
    "price": ALL_EXCHANGES,
    "oi": ALL_EXCHANGES,                       # 4家真历史 + 6家每日快照累积
    "funding": ALL_EXCHANGES - {"Coinbase"},   # Coinbase INTX 无 funding 产品，正常缺失
}
FRESH_HOURS = 30          # 交易所已出现过数据，但最新 ingested_at 超过这个时长 → 判定"停更"
PULL_LOG_STALE_MIN = 90   # 今日拉取日志超过这个分钟数没有新记录 → 判定"传输层可能挂了"

RUNTIME_DIR = Path("/mnt/e/data-pipeline") if not sys.platform.startswith("win") else Path("E:/data-pipeline")
PULL_LOG_DIR = RUNTIME_DIR / "logs"
CHECK_LOG_DIR = RUNTIME_DIR / "logs"


def _now_ms() -> int:
    return int(time.time() * 1000)


def check_pull_pipeline() -> list[str]:
    """检查 pull_outbox.sh 今日日志：存在性 + 近期活动 + 是否连续失败。"""
    issues: list[str] = []
    today = datetime.now().strftime("%Y%m%d")
    log_path = PULL_LOG_DIR / f"pull_{today}.log"
    if not log_path.exists():
        issues.append(f"ERROR: 今日拉取日志不存在 ({log_path})，Windows 计划任务「DataLake Pull Outbox」可能没在跑")
        return issues

    age_min = (time.time() - log_path.stat().st_mtime) / 60
    if age_min > PULL_LOG_STALE_MIN:
        issues.append(
            f"ERROR: 拉取日志已 {age_min:.0f} 分钟没有新内容(阈值 {PULL_LOG_STALE_MIN} 分钟)，"
            f"检查 Windows 计划任务「DataLake Pull Outbox」是否还在跑、VPS 是否可达"
        )

    tail = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-30:]
    if any("pull FAILED" in line for line in tail):
        issues.append("ERROR: 最近日志出现 'pull FAILED'，rsync 连续失败，检查 VPS 连通性/SSH 密钥")
    return issues


def check_dataset(con, dataset: str, expected: set[str]) -> tuple[list[str], list[str]]:
    """返回 (errors, warnings)。"""
    rows = con.execute(
        f"SELECT exchange, count(DISTINCT symbol) syms, count(*) n_rows, max(ingested_at) last_ingested "
        f"FROM tradfi_{dataset} GROUP BY 1"
    ).fetchall()
    by_ex = {r[0]: r for r in rows}
    errors: list[str] = []
    warnings: list[str] = []

    total_rows = sum(r[2] for r in rows)
    if total_rows == 0:
        errors.append(f"[{dataset}] ERROR: 整个数据集 0 行，检查 VPS to_parquet.py / 拉取是否正常")
        return errors, warnings

    for ex in sorted(expected - by_ex.keys()):
        warnings.append(f"[{dataset}] WARN: 缺少交易所 {ex}（快照型 OI 还没补上，或本来就没这个数据集，需人工确认）")

    now = _now_ms()
    for ex, (_, _syms, _n_rows, last_ing) in by_ex.items():
        if not last_ing:
            continue
        age_h = (now - last_ing) / 3_600_000
        if age_h > FRESH_HOURS:
            errors.append(f"[{dataset}] ERROR: {ex} 已 {age_h:.1f} 小时没有新数据落库(阈值 {FRESH_HOURS}h)，可能已停更")

    return errors, warnings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="额外打印 JSON 摘要")
    args = ap.parse_args()

    CHECK_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = CHECK_LOG_DIR / f"coverage_{datetime.now().strftime('%Y%m%d')}.log"

    lines = [f"=== coverage check {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ==="]
    all_errors: list[str] = list(check_pull_pipeline())
    all_warnings: list[str] = []

    con = lake.connect()
    for dataset, expected in EXPECTED.items():
        errs, warns = check_dataset(con, dataset, expected)
        all_errors += errs
        all_warnings += warns

    lines += all_warnings + all_errors
    if not all_errors and not all_warnings:
        lines.append("OK: 全部数据集正常，拉取管道活跃")

    text = "\n".join(lines)
    print(text)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(text + "\n\n")

    if args.json:
        print(jsonlib.dumps({"errors": all_errors, "warnings": all_warnings}, ensure_ascii=False))

    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main())
