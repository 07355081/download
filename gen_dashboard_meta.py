#!/usr/bin/env python3
"""为 dashboard 生成目录索引与数据契约清单。

产出两样东西，都以下划线开头，且一个文件都不往数据目录里加：

  _indexes/<dir>.json  每个数据目录的文件名清单。dashboard 设了 DATA_BASE_URL 之后
                       走 HTTP 读数据，而 HTTP 没有目录列表语义，listLocalJson()
                       就靠这份索引列目录。
  _manifest.json       数据契约：每个数据集的文件数、字节数、schema 指纹、时间范围。
                       提交进代码仓库后，"数据结构变没变" 就是一次 git diff。

索引刻意写在独立的 _indexes/ 子树而不是数据目录内：旧版 dashboard 的 listLocalJson()
会把目录里任何 *.json 都当数据文件解析，往数据目录塞索引会让线上页面拿它当 OHLC 用。
copy_to_dashboard.py 的 mirror-delete 也会把它当成"源里没有的多余文件"删掉。

用法：
    python3 gen_dashboard_meta.py                      # 写 /root/dashboard/public/json
    python3 gen_dashboard_meta.py --dry-run            # 只报告不落盘
    python3 gen_dashboard_meta.py --dest /path/to/json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_dashboard_json_freshness import scan_payload  # noqa: E402

DEFAULT_DEST = Path(os.environ.get("DASHBOARD_JSON_DEST", "/root/dashboard/public/json"))

INDEX_ROOT_NAME = "_indexes"
MANIFEST_NAME = "_manifest.json"

# 抽样解析的单文件大小上限。这台机器只有 3.5GB 内存且要跟线上容器共存，
# 解析一个 8MB 的 JSON 峰值就已经是几百 MB 的 Python 对象了，不能再放大。
MAX_SAMPLE_BYTES = 8 * 1024 * 1024

# 描述结构时的最大下钻深度，避免宽表把指纹撑成几千个键。
MAX_SHAPE_DEPTH = 3


def is_data_json(name: str) -> bool:
    """下划线开头的是索引/清单等元数据，不算数据集本身。"""
    return name.endswith(".json") and not name.startswith("_")


def atomic_write_json(payload: Any, dst: Path) -> int:
    """写 JSON 且绝不暴露半截文件（dashboard 在线读这棵树）。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)
    return len(text.encode("utf-8"))


def describe_shape(value: Any, depth: int = 0) -> Any:
    """把一份 payload 抽象成只含键名和类型的结构描述（不含任何数据值）。"""
    if isinstance(value, dict):
        if depth >= MAX_SHAPE_DEPTH:
            return "object"
        return {k: describe_shape(value[k], depth + 1) for k in sorted(map(str, value.keys()))}
    if isinstance(value, list):
        if not value:
            return []
        # 同构数组只描述首元素，避免 18k 行展开成 18k 份结构。
        return [describe_shape(value[0], depth + 1)]
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if value is None:
        return "null"
    return "string"


def fingerprint(shape: Any) -> str:
    canonical = json.dumps(shape, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]


def iso_day(value: date | None) -> str | None:
    return value.isoformat() if value else None


def collect_dirs(dest: Path) -> dict[str, list[str]]:
    """返回 {相对目录 posix 路径: [数据文件名]}，跳过 _indexes/ 自身。"""
    result: dict[str, list[str]] = {}
    for current, subdirs, files in os.walk(dest):
        cur = Path(current)
        rel = cur.relative_to(dest)
        if rel.parts and rel.parts[0] == INDEX_ROOT_NAME:
            subdirs[:] = []
            continue
        subdirs[:] = sorted(d for d in subdirs if d != INDEX_ROOT_NAME)
        names = sorted(n for n in files if is_data_json(n))
        if names:
            result[rel.as_posix()] = names
    return result


def sample_dataset(dest: Path, dirs: dict[str, list[str]], dataset: str, limit: int) -> dict[str, Any]:
    """抽样解析数据集里的少数几个文件，得出 schema 指纹与时间范围。"""
    candidates: list[Path] = []
    for rel_dir, names in dirs.items():
        top = rel_dir.split("/")[0] if rel_dir != "." else "."
        if top != dataset:
            continue
        base = dest / rel_dir if rel_dir != "." else dest
        candidates.extend(base / n for n in names)
    candidates.sort()
    if not candidates:
        return {}

    # 取首尾各一个：同一数据集内不同交易所/品种的结构应当一致，
    # 首尾能覆盖到命名两端，比随机抽样更容易复现。
    picks: list[Path] = [candidates[0]]
    if limit > 1 and len(candidates) > 1:
        picks.append(candidates[-1])
    picks = picks[:limit]

    shapes: list[str] = []
    sample_names: list[str] = []
    first_day: date | None = None
    last_day: date | None = None
    meta_day: date | None = None
    skipped_big = 0

    for path in picks:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > MAX_SAMPLE_BYTES:
            skipped_big += 1
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue

        sample_names.append(path.relative_to(dest).as_posix())
        shapes.append(fingerprint(describe_shape(payload)))

        scan = scan_payload(payload)
        if scan.days_min and (first_day is None or scan.days_min < first_day):
            first_day = scan.days_min
        if scan.days_max and (last_day is None or scan.days_max > last_day):
            last_day = scan.days_max
        # 快照类数据集（hyperliquid、tag）没有时间序列，只有 updated_at /
        # generated_at 这类元数据字段，退而用它回答"更新到哪天"。
        if scan.meta_max and (meta_day is None or scan.meta_max > meta_day):
            meta_day = scan.meta_max

        del payload

    info: dict[str, Any] = {
        "samples": sample_names,
        "schema_fingerprint": shapes[0] if shapes else None,
        "first_day": iso_day(first_day),
        "last_day": iso_day(last_day) or iso_day(meta_day),
        "last_day_source": "series" if last_day else ("metadata" if meta_day else None),
    }
    # 首尾指纹不一致说明同一数据集内部结构不统一，值得在 diff 里看见。
    if len(set(shapes)) > 1:
        info["schema_fingerprint_variants"] = sorted(set(shapes))
    if skipped_big:
        info["samples_skipped_oversize"] = skipped_big
    return info


def write_indexes(dest: Path, dirs: dict[str, list[str]], dry_run: bool) -> tuple[int, int]:
    """为每个数据目录写 _indexes/<dir>.json，并清掉源目录已消失的陈旧索引。"""
    index_root = dest / INDEX_ROOT_NAME
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    expected: set[Path] = set()
    written = total_bytes = 0

    for rel_dir, names in sorted(dirs.items()):
        if rel_dir == ".":
            continue  # 根目录下的散落 JSON 没有列目录需求
        target = index_root / f"{rel_dir}.json"
        expected.add(target)
        payload = {
            "dir": rel_dir,
            "count": len(names),
            "generated_at": generated_at,
            "files": names,
        }
        if dry_run:
            written += 1
            continue
        total_bytes += atomic_write_json(payload, target)
        written += 1

    removed = 0
    if index_root.is_dir():
        for stale in sorted(index_root.rglob("*.json")):
            if stale in expected:
                continue
            removed += 1
            if not dry_run:
                stale.unlink(missing_ok=True)
        if not dry_run:
            for leftover in index_root.rglob(".*.tmp"):
                leftover.unlink(missing_ok=True)
            # 自底向上删空目录
            for d in sorted((p for p in index_root.rglob("*") if p.is_dir()), reverse=True):
                if not any(d.iterdir()):
                    d.rmdir()

    if not dry_run:
        print(f"[indexes] wrote={written} removed={removed} bytes={total_bytes}")
    else:
        print(f"[indexes] WOULD write={written} remove={removed}")
    return written, removed


def build_manifest(dest: Path, dirs: dict[str, list[str]], samples: int) -> dict[str, Any]:
    datasets: dict[str, Any] = {}

    for rel_dir, names in dirs.items():
        top = rel_dir.split("/")[0] if rel_dir != "." else "."
        entry = datasets.setdefault(
            top, {"dirs": [], "file_count": 0, "total_bytes": 0}
        )
        entry["dirs"].append(rel_dir)
        entry["file_count"] += len(names)
        base = dest / rel_dir if rel_dir != "." else dest
        for name in names:
            try:
                entry["total_bytes"] += (base / name).stat().st_size
            except OSError:
                pass

    for name, entry in datasets.items():
        entry["dirs"] = sorted(entry["dirs"])
        entry.update(sample_dataset(dest, dirs, name, samples))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": str(dest),
        "dataset_count": len(datasets),
        "file_count": sum(e["file_count"] for e in datasets.values()),
        "total_bytes": sum(e["total_bytes"] for e in datasets.values()),
        "datasets": {k: datasets[k] for k in sorted(datasets)},
    }


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="dashboard 的 public/json 目录")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写任何文件")
    ap.add_argument("--samples", type=int, default=2, help="每个数据集抽样解析的文件数（默认 2）")
    ap.add_argument("--skip-manifest", action="store_true", help="只刷目录索引，不重算数据契约")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    dest: Path = args.dest
    if not dest.is_dir():
        raise SystemExit(f"[gen_dashboard_meta] dest not found: {dest}")

    dirs = collect_dirs(dest)
    print(f"[scan] dirs={len(dirs)} files={sum(len(v) for v in dirs.values())} dest={dest}")

    write_indexes(dest, dirs, args.dry_run)

    if args.skip_manifest:
        return

    manifest = build_manifest(dest, dirs, max(1, args.samples))
    if args.dry_run:
        print(
            f"[manifest] WOULD write datasets={manifest['dataset_count']} "
            f"files={manifest['file_count']}"
        )
        return

    size = atomic_write_json(manifest, dest / MANIFEST_NAME)
    print(
        f"[manifest] wrote {MANIFEST_NAME} datasets={manifest['dataset_count']} "
        f"files={manifest['file_count']} bytes={size}"
    )


if __name__ == "__main__":
    main()
