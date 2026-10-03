"""JSON 产物 → Parquet 分块派生器(下载中转管道 · VPS 侧)。

本文件是 VPS 部署脚本的只读镜像(供本机版本控制/审查),真源在 VPS
/root/data-download/tradfi/to_parquet.py。改动需要手动同步上去,这里改了不会自动生效。

契约(本机 rsync 拉取据此设计):
- 读取 tradfi/output/json/<module>/*.json,每个文件转一个 parquet。
- 输出布局:<outbox>/<module>/<EXCHANGE>/<EXCHANGE>_<SYMBOL>_<interval>.parquet
- 原子写:先写 <outbox>/.staging/<uuid>.parquet,写完 os.replace() 原子改名到最终路径
  (同一文件系统 rename 原子)。因此 outbox 下(除 .staging/ 外)出现的任何 *.parquet
  都是**完整文件**,本机拉取永不会拿到写一半的分块。无需 .done 标记。
- 列:原始行列(time/open/high/low/close/volume_usd 等)+ 常量元列
  (exchange/symbol/base_asset/sector/interval/form)+ ingested_at(本次派生时刻 ms,PIT/vintage)。
- 压缩:zstd(parquet 内建,已很小,本机 rsync 不必再 -z)。

用法:
  ./.venv/bin/python tradfi/to_parquet.py                  # 转全部模块
  ./.venv/bin/python tradfi/to_parquet.py --modules tradfi-price
  ./.venv/bin/python tradfi/to_parquet.py --outbox /root/outbox
"""
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ROOT_DIR = Path(__file__).resolve().parent
JSON_DIR = ROOT_DIR / "output" / "json"
DEFAULT_OUTBOX = Path("/root/outbox")
MODULES = ["tradfi-price", "tradfi-oi", "tradfi-funding"]
META_KEYS = ["exchange", "symbol", "base_asset", "sector", "interval", "form"]


def _safe_seg(value: str) -> str:
    return "".join(c if (c.isalnum() or c in "._-") else "-" for c in (value or ""))


def convert_file(src: Path, module: str, outbox: Path, ingested_at: int) -> tuple[bool, int]:
    """转一个 json → parquet(原子)。返回 (是否写出, 行数)。"""
    try:
        doc = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, 0
    rows = doc.get("data") if isinstance(doc, dict) else doc
    if not isinstance(rows, list) or not rows:
        return False, 0

    table = pa.Table.from_pylist(rows)
    n = table.num_rows
    meta = {k: (doc.get(k) if isinstance(doc, dict) else None) for k in META_KEYS}
    for k in META_KEYS:
        table = table.append_column(k, pa.array([meta.get(k)] * n, type=pa.string()))
    table = table.append_column("ingested_at", pa.array([ingested_at] * n, type=pa.int64()))

    exchange = _safe_seg(str(meta.get("exchange") or "unknown"))
    dest_dir = outbox / module / exchange
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / (src.stem + ".parquet")

    staging = outbox / ".staging"
    staging.mkdir(parents=True, exist_ok=True)
    tmp = staging / f"{uuid.uuid4().hex}.parquet"
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, final)  # 原子:此刻起 outbox 里这个分块才可见且完整
    return True, n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modules", nargs="*", default=MODULES, help=f"默认全部:{MODULES}")
    ap.add_argument("--outbox", default=str(DEFAULT_OUTBOX))
    ap.add_argument("--json-dir", default=str(JSON_DIR))
    args = ap.parse_args()

    outbox = Path(args.outbox)
    json_dir = Path(args.json_dir)
    ingested_at = int(time.time() * 1000)

    grand_files = grand_rows = 0
    for module in args.modules:
        src_dir = json_dir / module
        if not src_dir.is_dir():
            print(f"[{module}] 跳过(无目录 {src_dir})")
            continue
        files = sorted(src_dir.glob("*.json"))
        wrote = rows_sum = 0
        for src in files:
            ok, n = convert_file(src, module, outbox, ingested_at)
            if ok:
                wrote += 1
                rows_sum += n
        grand_files += wrote
        grand_rows += rows_sum
        print(f"[{module}] 转出 {wrote}/{len(files)} 文件, {rows_sum} 行 → {outbox / module}")

    print(f"\n合计:{grand_files} 个 parquet, {grand_rows} 行。outbox={outbox}")


if __name__ == "__main__":
    main()
