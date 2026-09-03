"""Run the UNI Burn (Dune) pipeline.

目前只有一步(直接从 Dune 读结果写 JSON,无需 CSV 中间层),此脚本作为统一入口,
与其它模块 run_all.py 风格保持一致,便于 run_misc_daily.sh 调用与后续扩展。

Usage:
  python run_all.py
  python run_all.py --only daily_by_chain
  python run_all.py -- --dry-run          # `--` 之后的参数原样透传给 download.py
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD = BASE_DIR / "download.py"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run UNI Burn Dune pipeline")
    parser.add_argument("--python", default=sys.executable, help="Python executable for subprocess")
    parser.add_argument("--api-key", default=None, help="透传给 download.py 的 Dune API key")
    parser.add_argument("--only", default=None, help="透传给 download.py 的单个 query name")
    args, passthrough = parser.parse_known_args()

    cmd = [args.python, str(DOWNLOAD)]
    if args.api_key:
        cmd += ["--api-key", args.api_key]
    if args.only:
        cmd += ["--only", args.only]
    cmd += [a for a in passthrough if a != "--"]

    print("=== UNI Burn: download from Dune ===", flush=True)
    print("$ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)
    print("\nDONE. outputs under uni-burn/output/json/", flush=True)


if __name__ == "__main__":
    main()
