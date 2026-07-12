from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STEP_DEFILLAMA = BASE_DIR / "download_defillama.py"
STEP_CONVERT = BASE_DIR / "csv_to_json.py"


def run_step(cmd: list[str], title: str) -> None:
    print(f"\n=== {title} ===", flush=True)
    print("$ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run stablecoin pipeline: DefiLlama download + csv_to_json",
    )
    parser.add_argument("--python", default=sys.executable, help="Python executable for subprocesses")
    parser.add_argument("--skip-defillama", action="store_true", help="Skip download_defillama.py")
    parser.add_argument("--skip-convert", action="store_true", help="Skip csv_to_json.py")
    parser.add_argument("--only-chains", default="", help="Pass-through --only for download_defillama.py")
    parser.add_argument(
        "--full-defillama",
        action="store_true",
        help="Pass --full to download_defillama.py (force re-download all chain history)",
    )
    parser.add_argument("--compact", action="store_true", help="Pass-through compact for csv_to_json.py")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    py = args.python

    if not args.skip_defillama:
        cmd = [py, str(STEP_DEFILLAMA)]
        if args.only_chains:
            cmd.extend(["--only", str(args.only_chains)])
        if args.full_defillama:
            cmd.append("--full")
        run_step(cmd, "Step 1/2: download stablecoin market cap (DefiLlama)")

    if not args.skip_convert:
        cmd = [py, str(STEP_CONVERT)]
        if args.compact:
            cmd.append("--compact")
        run_step(cmd, "Step 2/2: convert stablecoin CSV to JSON")

    print("\nDONE. outputs under stablecoin/output/", flush=True)


if __name__ == "__main__":
    main()
