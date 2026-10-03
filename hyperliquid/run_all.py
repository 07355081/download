"""Run the Hyperliquid pipeline.

两步,都直接写 JSON(无需 CSV 中间层):
  download.py        ASXN 的 HIP-3 / HIP-4 小表(缓存超过 36h 才自己执行);按币种走官方接口
  download_fees.py   实测手续费(DefiLlama)+ 成交量拆分与 HyperEVM DEX(_dune.py)

第二步失败不影响第一步已写好的文件,所以两步分开报错、整体以非零码结束,交由
run_misc_daily.sh 的 [WARN] 分支记录。

Usage:
  python run_all.py                     # 两步都跑
  python run_all.py --only hip4         # 只跑 download.py 的某个 query
  python run_all.py --skip-fees         # 只跑第一步
  python run_all.py --fees-only         # 只跑第二步
  python run_all.py -- --no-execute     # `--` 之后的参数原样透传给 download_fees.py
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD = BASE_DIR / "download.py"
DOWNLOAD_FEES = BASE_DIR / "download_fees.py"


def run(label: str, cmd: list[str]) -> bool:
    print(f"=== {label} ===", flush=True)
    print("$ " + " ".join(cmd), flush=True)
    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as exc:
        print(f"[WARN] {label} 失败(exit={exc.returncode})", flush=True)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Hyperliquid pipeline")
    parser.add_argument("--python", default=sys.executable, help="Python executable for subprocess")
    parser.add_argument("--api-key", default=None, help="透传的 Dune API key")
    parser.add_argument("--only", default=None, help="透传给 download.py 的单个 query name")
    parser.add_argument("--skip-fees", action="store_true", help="不跑 download_fees.py")
    parser.add_argument("--fees-only", action="store_true", help="只跑 download_fees.py")
    args, passthrough = parser.parse_known_args()
    extra = [a for a in passthrough if a != "--"]

    results: list[bool] = []

    if not args.fees_only:
        cmd = [args.python, str(DOWNLOAD)]
        if args.api_key:
            cmd += ["--api-key", args.api_key]
        if args.only:
            cmd += ["--only", args.only]
        results.append(run("Hyperliquid: HIP-3/HIP-4 from Dune", cmd))

    if not args.skip_fees:
        cmd = [args.python, str(DOWNLOAD_FEES)]
        if args.api_key:
            cmd += ["--api-key", args.api_key]
        cmd += extra
        results.append(run("Hyperliquid: fees / volume split / HyperEVM DEX", cmd))

    print("\nDONE. outputs under hyperliquid/output/json/", flush=True)
    if not all(results):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
