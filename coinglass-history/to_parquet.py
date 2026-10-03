"""JSON 产物 → Parquet 分块派生器(下载中转管道 · VPS 侧,coinglass-history)。

契约(与 tradfi/to_parquet.py 完全一致,本机 pull_outbox.sh 不用改一行代码):
- 读取 <module>/output/json/*.json(module = coinglass-history 下每个子目录,如
  futures-price-history/etf-list/option-max-pain-history 等),每个文件转一个 parquet。
- 输出布局:<outbox>/<module>/[<exchange>/]<stem>.parquet
  (该文件 meta 含非空 exchange 时按 exchange 分子目录,减少单目录文件数;否则直接落 <module>/ 下)
- 原子写:先写 <outbox>/.staging/<uuid>.parquet,写完 os.replace() 原子改名到最终路径
  (同一文件系统 rename 原子)。outbox 下(除 .staging/ 外)出现的任何 *.parquet 都是完整文件。

coinglass-history 16 个子模块 json 结构不完全一致,已归纳为 3 类,自动识别(见 _extract_rows):
  A) 候选行 = doc['data'](list) 或 doc['snapshots'](list) —— 覆盖 14/16 模块,含 etf-list、
     option-max-pain-history;行内嵌套 dict/list 字段(如 etf-list 的 asset_details)就地
     json.dumps 成字符串,保证能落 parquet(DuckDB 可用 json_extract 再解析)。
  B) etf-premium-discount-history:data=[{timestamp, list:[{ticker,...}]}] 按天嵌套 —— 展开成
     每 (timestamp, ticker) 一行,粒度对齐其余模块。
  C) option-exchange-{oi,vol}-history:data={time_list, price_list, data_map:{exchange:[...]}}
     宽表 —— pivot 成 (time, exchange, value, price) 长表。

列:原始行列 + 常量元列(module + doc 顶层其余标量键,如 exchange/symbol/interval/market/
ticker/unit/range)+ ingested_at(本次派生时刻 ms,PIT/vintage)。压缩:zstd。

用法:
  ./.venv/bin/python coinglass-history/to_parquet.py
  ./.venv/bin/python coinglass-history/to_parquet.py --modules futures-price-history spot-price-history
  ./.venv/bin/python coinglass-history/to_parquet.py --outbox /root/outbox
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _watermark as WM  # noqa: E402

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTBOX = Path("/root/outbox")
SKIP_DIRS = {"symbols", "__pycache__"}


def _safe_seg(value: str) -> str:
    return "".join(c if (c.isalnum() or c in "._-") else "-" for c in (value or ""))


def discover_modules(root: Path) -> list[str]:
    """只取含 output/json/ 的子目录(与 run_all.py 的模块发现口径一致)。"""
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("_") or d.name in SKIP_DIRS:
            continue
        if (d / "output" / "json").is_dir():
            out.append(d.name)
    return out


def _jsonify_nested(row: dict) -> dict:
    """行内嵌套 dict/list 值就地转 JSON 字符串,保证能塞进 parquet 的标量列。"""
    out = {}
    for k, v in row.items():
        out[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
    return out


def _extract_rows(doc: dict) -> list[dict] | None:
    """按 doc 结构识别行:C)宽表 pivot / B)嵌套展开 / A)直取 data|snapshots。"""
    data = doc.get("data")

    if isinstance(data, dict) and "time_list" in data:
        time_list = data.get("time_list") or []
        price_list = data.get("price_list") or []
        data_map = data.get("data_map") or {}
        rows = []
        for idx, t in enumerate(time_list):
            price = price_list[idx] if idx < len(price_list) else None
            for ex, vals in data_map.items():
                v = vals[idx] if isinstance(vals, list) and idx < len(vals) else None
                if v is None:
                    continue
                rows.append({"time": t, "exchange": ex, "value": v, "price": price})
        return rows

    rows_src = data if isinstance(data, list) else doc.get("snapshots")
    if not isinstance(rows_src, list) or not rows_src:
        return None

    first = rows_src[0]
    if isinstance(first, dict) and isinstance(first.get("list"), list) and "timestamp" in first:
        flat = []
        for item in rows_src:
            ts = item.get("timestamp")
            for sub in item.get("list") or []:
                if isinstance(sub, dict):
                    flat.append({"timestamp": ts, **sub})
        return flat

    return rows_src


def convert_file(src: Path, module: str, outbox: Path, ingested_at: int) -> tuple[bool, int]:
    """转一个 json → parquet(原子)。返回 (是否写出, 行数)。"""
    try:
        doc = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, 0
    if not isinstance(doc, dict):
        return False, 0
    rows = _extract_rows(doc)
    if not rows:
        return False, 0
    rows = [_jsonify_nested(r) for r in rows if isinstance(r, dict)]
    if not rows:
        return False, 0

    try:
        table = pa.Table.from_pylist(rows)
    except (pa.lib.ArrowInvalid, pa.lib.ArrowTypeError):
        # 极少数字段同名但类型不一致(偶尔字符串偶尔数字):整表转字符串兜底,不丢数据。
        rows = [{k: (v if v is None else str(v)) for k, v in r.items()} for r in rows]
        table = pa.Table.from_pylist(rows)
    n = table.num_rows

    meta = {"module": module}
    for k, v in doc.items():
        if k in ("data", "snapshots", "code", "msg"):
            continue
        if isinstance(v, (str, int, float)) or v is None:
            meta[k] = v
    existing_cols = set(table.schema.names)
    for k, v in meta.items():
        if k in existing_cols:  # 行级列优先,不覆盖(如 pivot 后的 exchange)
            continue
        table = table.append_column(k, pa.array([str(v) if v is not None else None] * n, type=pa.string()))
    if "ingested_at" not in existing_cols:
        table = table.append_column("ingested_at", pa.array([ingested_at] * n, type=pa.int64()))

    exchange = meta.get("exchange")
    dest_dir = outbox / module / _safe_seg(str(exchange)) if exchange else outbox / module
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
    ap.add_argument("--modules", nargs="*", default=None, help="默认自动发现全部 16 个模块")
    ap.add_argument("--outbox", default=str(DEFAULT_OUTBOX))
    ap.add_argument("--root", default=str(ROOT_DIR))
    args = ap.parse_args()

    outbox = Path(args.outbox)
    root = Path(args.root)
    modules = args.modules or discover_modules(root)
    ingested_at = int(time.time() * 1000)

    gate = WM.Gate(outbox)
    grand_files = grand_rows = 0
    stopped = False
    for module in modules:
        if gate.blocked():
            print(f"[watermark BLOCK] {gate.status().reason};停止派生,剩余 JSON 留待下轮")
            stopped = True
            break
        src_dir = root / module / "output" / "json"
        if not src_dir.is_dir():
            print(f"[{module}] 跳过(无目录 {src_dir})")
            continue
        files = sorted(src_dir.glob("*.json"))
        wrote = rows_sum = 0
        for i, src in enumerate(files):
            if i % 200 == 0 and gate.blocked():  # 大模块中途也复查,避免撑爆 outbox
                print(f"[watermark BLOCK] {gate.status().reason};中止 {module} 剩余派生")
                stopped = True
                break
            ok, n = convert_file(src, module, outbox, ingested_at)
            if ok:
                wrote += 1
                rows_sum += n
        grand_files += wrote
        grand_rows += rows_sum
        print(f"[{module}] 转出 {wrote}/{len(files)} 文件, {rows_sum} 行 → {outbox / module}")
        if stopped:
            break

    print(f"\n合计:{grand_files} 个 parquet, {grand_rows} 行。outbox={outbox}"
          + ("  [因水位提前停止]" if stopped else ""))


if __name__ == "__main__":
    main()
