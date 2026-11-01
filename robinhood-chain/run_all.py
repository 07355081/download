"""Run the Robinhood Chain pipeline (Dune metrics + DefiLlama fees).

Usage:
  python run_all.py
  python run_all.py --only rwa_aum
  python run_all.py --skip-fees
  python run_all.py --fees-only
  python run_all.py -- --dry-run          # `--` 之后透传给 download.py
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD = BASE_DIR / "download.py"
DOWNLOAD_FEES = BASE_DIR / "download_fees.py"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Robinhood Chain pipeline")
    parser.add_argument("--python", default=sys.executable, help="Python executable for subprocess")
    parser.add_argument("--api-key", default=None, help="透传给 download.py 的 Dune API key")
    parser.add_argument("--only", default=None, help="透传给 download.py 的单个 query name")
    parser.add_argument("--skip-fees", action="store_true", help="不跑 DefiLlama 手续费")
    parser.add_argument("--fees-only", action="store_true", help="只跑 DefiLlama 手续费")
    args, passthrough = parser.parse_known_args()
    passthrough = [a for a in passthrough if a != "--"]

    if not args.fees_only:
        cmd = [args.python, str(DOWNLOAD)]
        if args.api_key:
            cmd += ["--api-key", args.api_key]
        if args.only:
            cmd += ["--only", args.only]
        cmd += passthrough
        print("=== Robinhood Chain: download from Dune ===", flush=True)
        print("$ " + " ".join(cmd), flush=True)
        subprocess.run(cmd, check=True)

    if not args.skip_fees and args.only is None:
        fees_cmd = [args.python, str(DOWNLOAD_FEES)]
        if "--dry-run" in passthrough:
            fees_cmd.append("--dry-run")
        print("=== Robinhood Chain: fees from DefiLlama ===", flush=True)
        print("$ " + " ".join(fees_cmd), flush=True)
        subprocess.run(fees_cmd, check=True)

    print("\nDONE. outputs under robinhood-chain/output/json/", flush=True)


if __name__ == "__main__":
    main()
