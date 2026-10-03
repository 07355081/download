"""JSON 产物 → Parquet 分块派生器(下载中转管道 · VPS 侧,非 coinglass/tradfi 的杂项模块)。

覆盖模块(默认):btc-index、crypto-treasuries、mining-shutdown-price、options、stablecoin。
契约与 coinglass-history/to_parquet.py、tradfi/to_parquet.py 一致(本机 pull_outbox.sh 零改动):
- 递归读取 <module>/output/json/**/*.json,每个可表格化的文件转一个 parquet。
- 输出布局:<outbox>/<module>/<相对 output/json 的子路径>.parquet
  (保留 btc-index/coinglass/ 这类子目录,便于本机湖按模块归位)。
- 原子写:先写 <outbox>/.staging/<uuid>.parquet,写完 os.replace() 原子改名到最终路径。

这些模块 json 顶层统一是 {code?,msg?,...,data:...},data 结构分几类,自动识别(见 _extract_rows):
  A) data 为 list[dict] —— options / stablecoin / mining-shutdown-price(时间序列/快照,直取)。
  B) data 为 {time_list:[...], data_list:[dict,...]} 并行数组 —— btc-index/coinglass 指标,zip 成
     每行 {time: time_list[i], **data_list[i]}。
  C) data 为 {time_list, price_list, data_map:{k:[...]}} 宽表 —— pivot 成长表(与 coinglass 同款兜底)。
  D) data 为 {rows:[dict,...]} —— cex 风格宽表(此处备用)。
  其余(如 crypto-treasuries 的嵌套 overview/entity dict、index.json)非表格,返回 None 优雅跳过,
  json 仍照常经 copy_to_dashboard 上网页,只是不进 parquet 湖。

列:原始行列 + 常量元列(module + source(相对 stem) + doc 顶层其余标量键)+ ingested_at(派生时刻 ms,PIT)。
压缩:zstd。

用法:
  ./.venv/bin/python to_parquet_misc.py
  ./.venv/bin/python to_parquet_misc.py --modules options stablecoin
  ./.venv/bin/python to_parquet_misc.py --outbox /root/outbox
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _watermark as WM  # noqa: E402

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTBOX = Path("/root/outbox")
DEFAULT_MODULES = (
    "btc-index",
    "crypto-treasuries",
    "mining-shutdown-price",
    "options",
    "stablecoin",
)

# 部分模块 output/json 里有"带日期归档 + 稳定 latest"两份(如 options 的 dvol_*_YYYYMMDD_* 与
# dvol_*_latest_*):dashboard/量化只认稳定 latest,归档逐日变名会污染 parquet 湖,故只挑 latest。
MODULE_INCLUDE_GLOBS: dict[str, str] = {
    "options": "*_latest_*.json",
}


def _safe_seg(value: str) -> str:
    return "".join(c if (c.isalnum() or c in "._-") else "-" for c in (value or ""))


def _jsonify_nested(row: dict) -> dict:
    """行内嵌套 dict/list 值就地转 JSON 字符串,保证能塞进 parquet 的标量列。"""
    out = {}
    for k, v in row.items():
        out[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
    return out


def _extract_rows(doc: dict) -> list[dict] | None:
    """按 doc 结构识别行:A)data 列表 / B)time_list+data_list 并行 / C)宽表 pivot / D)data.rows。"""
    data = doc.get("data")

    # A) data 直接是行列表
    if isinstance(data, list):
        if not data:
            return None
        first = data[0]
        if isinstance(first, dict):
            return data
        # A') 位置数组行(options DVOL:[date,open,high,low,close];其余按 c0..cn 兜底)
        if isinstance(first, (list, tuple)):
            ncol = len(first)
            cols = (
                ["date", "open", "high", "low", "close"]
                if ncol == 5
                else [f"c{i}" for i in range(ncol)]
            )
            return [
                {cols[i]: (r[i] if i < len(r) else None) for i in range(len(cols))}
                for r in data
                if isinstance(r, (list, tuple))
            ] or None
        return None

    if isinstance(data, dict):
        # C) coinglass 同款宽表 {time_list, price_list, data_map}
        if "time_list" in data and "data_map" in data:
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
            return rows or None

        # B) btc-index 并行数组 {time_list, data_list}
        if "time_list" in data and "data_list" in data:
            time_list = data.get("time_list") or []
            data_list = data.get("data_list") or []
            rows = []
            for idx, item in enumerate(data_list):
                t = time_list[idx] if idx < len(time_list) else None
                if isinstance(item, dict):
                    rows.append({"time": t, **item})
                else:
                    rows.append({"time": t, "value": item})
            return rows or None

        # D) cex 风格宽表 {rows:[...]}(备用)
        rows_src = data.get("rows")
        if isinstance(rows_src, list) and rows_src and isinstance(rows_src[0], dict):
            return rows_src

    return None


def convert_file(src: Path, module: str, rel: Path, outbox: Path, ingested_at: int) -> tuple[bool, int]:
    """转一个 json → parquet(原子)。rel 为相对 output/json 的路径。返回 (是否写出, 行数)。"""
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
        rows = [{k: (v if v is None else str(v)) for k, v in r.items()} for r in rows]
        table = pa.Table.from_pylist(rows)
    n = table.num_rows

    source = rel.with_suffix("").as_posix()
    meta = {"module": module, "source": source}
    for k, v in doc.items():
        if k in ("data", "snapshots", "code", "msg"):
            continue
        if isinstance(v, (str, int, float)) or v is None:
            meta[k] = v
    existing_cols = set(table.schema.names)
    for k, v in meta.items():
        if k in existing_cols:  # 行级列优先,不覆盖
            continue
        table = table.append_column(k, pa.array([str(v) if v is not None else None] * n, type=pa.string()))
    if "ingested_at" not in existing_cols:
        table = table.append_column("ingested_at", pa.array([ingested_at] * n, type=pa.int64()))

    dest_dir = outbox / module / rel.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / (src.stem + ".parquet")

    staging = outbox / ".staging"
    staging.mkdir(parents=True, exist_ok=True)
    tmp = staging / f"{uuid.uuid4().hex}.parquet"
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, final)
    return True, n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modules", nargs="*", default=None, help=f"默认:{', '.join(DEFAULT_MODULES)}")
    ap.add_argument("--outbox", default=str(DEFAULT_OUTBOX))
    ap.add_argument("--root", default=str(ROOT_DIR))
    args = ap.parse_args()

    outbox = Path(args.outbox)
    root = Path(args.root)
    modules = args.modules or list(DEFAULT_MODULES)
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
        files = sorted(src_dir.rglob(MODULE_INCLUDE_GLOBS.get(module, "*.json")))
        wrote = rows_sum = skipped = 0
        for i, src in enumerate(files):
            if i % 200 == 0 and gate.blocked():
                print(f"[watermark BLOCK] {gate.status().reason};中止 {module} 剩余派生")
                stopped = True
                break
            rel = src.relative_to(src_dir)
            ok, nrows = convert_file(src, module, rel, outbox, ingested_at)
            if ok:
                wrote += 1
                rows_sum += nrows
            else:
                skipped += 1
        grand_files += wrote
        grand_rows += rows_sum
        note = f", 跳过 {skipped}(非表格)" if skipped else ""
        print(f"[{module}] 转出 {wrote}/{len(files)} 文件, {rows_sum} 行{note} → {outbox / module}")
        if stopped:
            break

    print(f"\n合计:{grand_files} 个 parquet, {grand_rows} 行。outbox={outbox}"
          + ("  [因水位提前停止]" if stopped else ""))


if __name__ == "__main__":
    main()
